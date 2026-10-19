"""CC-803 R02: valida resolução hash-bound de catálogo no PostgreSQL descartável."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete

from riftlab.analyses import application
from riftlab.analyses.models import AnalysisModel
from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.catalog.models import CatalogIdentityModel, CatalogVersionModel, ItemModel
from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.dataset.rows import _catalog
from riftlab.decisions.models import DecisionExtractionModel, DecisionPointModel
from riftlab.ingestion.models import MatchModel, ParticipantModel
from riftlab.ranking.engine import DecisionFacts
from riftlab.shared.db import unit_of_work
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
from riftlab.timeline.models import DecisionStateModel, MatchReconstructionModel, TimelineEventModel


@pytest.mark.asyncio
async def test_postgres_resolves_inherited_source_by_content_hash_without_source_uuid(
    session_factory, tmp_path, monkeypatch
) -> None:
    """A UUID da origem não viaja no companion: o hash exato resolve a linha persistida."""
    source_id, destination_id = uuid.uuid4(), uuid.uuid4()
    source_sha = hashlib.sha256(b"cc803-r02-source-content").hexdigest()
    destination_sha = hashlib.sha256(b"cc803-r02-destination-content").hexdigest()
    raw_hash = hashlib.sha256(b"cc803-r02-raw").hexdigest()
    source = CatalogVersion(
        source_id, "16.19", "16.19.1", source_sha, "cc803-map-v1", raw_hash, "curated_demo_v1"
    )
    destination = CatalogVersion(
        destination_id,
        "16.20",
        "16.20.1",
        destination_sha,
        "cc803-map-v1",
        hashlib.sha256(b"cc803-r02-destination-raw").hexdigest(),
        "curated_demo_v1",
    )
    catalog_items = [
        Item(
            source_id,
            100001,
            400,
            [],
            {"name": "componente sintético A"},
            {"completed": False, "purchasable": True, "maps": [11]},
        ),
        Item(
            source_id,
            100002,
            350,
            [],
            {"name": "componente sintético B"},
            {"completed": False, "purchasable": True, "maps": [11]},
        ),
        Item(
            source_id,
            100010,
            1300,
            [{"itemId": 100001, "quantity": 2}, {"itemId": 100002, "quantity": 1}],
            {"name": "item sintético"},
            {"completed": True, "purchasable": True, "maps": [11]},
        ),
    ]
    try:
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyCatalogRepository(session)
            await repo.add_catalog_version(source)
            for item in catalog_items:
                await repo.add_item(item)
            await repo.add_catalog_version(destination)
            for item in catalog_items:
                await repo.add_item(
                    Item(
                        destination_id,
                        item.item_id,
                        item.price,
                        item.recipe,
                        item.attributes,
                        item.flags,
                    )
                )
        ranking = tmp_path / "ranking"
        ranking.mkdir()
        artifact = {
            "version": "popularity_16_19_v2",
            "gamePatch": "16.19",
            "datasetSha256": hashlib.sha256(b"frozen-training-fixture").hexdigest(),
            "cells": {"9000|1": {"100010": 19}},
        }
        artifact_path = ranking / "popularity-16.19-v2.json"
        artifact_bytes = (json.dumps(artifact, separators=(",", ":")) + "\n").encode()
        artifact_path.write_bytes(artifact_bytes)
        identity = {
            "gamePatch": source.game_patch,
            "dataDragonVersion": source.data_dragon_version,
            "sha256": source.sha256,
            "mappingVersion": source.mapping_version,
            "rawHash": source.raw_hash,
            "curationRulesVersion": source.curation_rules_version,
        }
        (ranking / "popularity-16.19-v2.provenance.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "artifactSha256": hashlib.sha256(artifact_bytes).hexdigest(),
                    "datasetSha256": artifact["datasetSha256"],
                    "sourceCatalogIdentity": identity,
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        configs = tmp_path / "configs"
        configs.mkdir()
        (configs / "ranking").symlink_to(ranking, target_is_directory=True)

        async with session_factory() as session:
            found = await SqlAlchemyCatalogRepository(session).find_catalog_version_by_content_hash(
                source_sha
            )
            assert found is not None and found.id == source_id
            source_bundle = await _catalog(session, found.id)
            destination_bundle = await _catalog(session, destination_id)
            own = await application._popularity_for_analysis(
                session,
                configs,
                destination.game_patch,
                DecisionFacts(9000, 1, None, None),
                destination_bundle,
                None,
            )

        popularity, support, reasons, reference = own
        assert popularity is not None
        assert popularity.support(DecisionFacts(9000, 1, None, None)) == 19
        assert support == 19
        assert reasons == ["POPULARITY_INHERITED"]
        assert reference["type"] == "INHERITED"
        assert reference["catalogId"] == str(source_id)
        assert source_bundle.catalog.sha256 == identity["sha256"]

        # Uma alteração do hash congelado não pode cair no canônico/outro catálogo.
        bad_identity = {**identity, "sha256": hashlib.sha256(b"other-content").hexdigest()}
        sidecar = ranking / "popularity-16.19-v2.provenance.json"
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
        manifest["sourceCatalogIdentity"] = bad_identity
        sidecar.write_text(json.dumps(manifest), encoding="utf-8")
        async with session_factory() as session:
            (
                _,
                failed_support,
                failed_reasons,
                failed_reference,
            ) = await application._popularity_for_analysis(
                session,
                configs,
                destination.game_patch,
                DecisionFacts(9000, 1, None, None),
                destination_bundle,
                None,
            )
        assert failed_support == 0
        assert failed_reasons == ["POPULARITY_SOURCE_CATALOG_NOT_FOUND"]
        assert failed_reference["type"] == "UNAVAILABLE"
    finally:
        async with unit_of_work(session_factory) as session:
            for catalog_id in (source_id, destination_id):
                await session.execute(delete(ItemModel).where(ItemModel.catalog_id == catalog_id))
                await session.execute(
                    delete(CatalogVersionModel).where(CatalogVersionModel.id == catalog_id)
                )


@pytest.mark.asyncio
async def test_create_analysis_persists_point_catalog_inheritance_and_idempotent_history(
    session_factory, tmp_path, monkeypatch
) -> None:
    """Serviço real: catálogo do ponto prevalece ao canônico e análise persiste após fonte mudar."""
    source_id, point_catalog_id, canonical_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    source_sha = hashlib.sha256(b"cc803-r02-service-source").hexdigest()
    source_raw = hashlib.sha256(b"cc803-r02-service-raw").hexdigest()
    destination_sha = hashlib.sha256(b"cc803-r02-point-catalog").hexdigest()
    canonical_sha = hashlib.sha256(b"cc803-r02-different-canonical").hexdigest()
    match_id = f"CC803-R02-{uuid.uuid4().hex}"
    state_id, extraction_id, decision_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    source = CatalogVersion(
        source_id,
        "16.19",
        "16.19.1",
        source_sha,
        "cc803-service-map",
        source_raw,
        "curated_demo_v1",
    )
    point_catalog = CatalogVersion(
        point_catalog_id,
        "16.20",
        "16.20.1",
        destination_sha,
        "cc803-service-map",
        hashlib.sha256(b"cc803-r02-point-raw").hexdigest(),
        "curated_demo_v1",
    )
    # O canônico deliberadamente não tem item principal: se o serviço consultar
    # canônico em vez do catalog_id persistido no ponto, a análise não fica AVAILABLE.
    canonical = CatalogVersion(
        canonical_id,
        "16.20",
        "16.20.1",
        canonical_sha,
        "cc803-service-map",
        hashlib.sha256(b"cc803-r02-canonical-raw").hexdigest(),
        "curated_demo_v1",
    )
    component_a = Item(
        source_id,
        100001,
        400,
        [],
        {"name": "componente A"},
        {"completed": False, "purchasable": True, "maps": [11]},
    )
    component_b = Item(
        source_id,
        100002,
        350,
        [],
        {"name": "componente B"},
        {"completed": False, "purchasable": True, "maps": [11]},
    )
    main = Item(
        source_id,
        100010,
        1300,
        [{"itemId": 100001, "quantity": 2}, {"itemId": 100002, "quantity": 1}],
        {"name": "principal sintético"},
        {"completed": True, "purchasable": True, "maps": [11]},
    )
    items = [component_a, component_b, main]
    body = {"decisionId": str(decision_id), "objective": {"priority": "BALANCED"}}
    key = f"cc803-r02-{uuid.uuid4()}"

    async def test_health(*_args, **_kwargs):
        return {"status": "OK"}

    monkeypatch.setattr(application, "health_for_state", test_health)
    try:
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyCatalogRepository(session)
            await repo.add_catalog_version(source)
            for item in items:
                await repo.add_item(
                    Item(
                        source_id,
                        item.item_id,
                        item.price,
                        item.recipe,
                        item.attributes,
                        item.flags,
                    )
                )
            await repo.add_catalog_version(point_catalog)
            for item in items:
                await repo.add_item(
                    Item(
                        point_catalog_id,
                        item.item_id,
                        item.price,
                        item.recipe,
                        item.attributes,
                        item.flags,
                    )
                )
            await repo.add_catalog_version(canonical)
            for item in (component_a, component_b):
                await repo.add_item(
                    Item(
                        canonical_id,
                        item.item_id,
                        item.price,
                        item.recipe,
                        item.attributes,
                        item.flags,
                    )
                )
            await repo.mark_canonical(canonical_id)
            session.add(
                MatchModel(
                    match_id=match_id,
                    queue=420,
                    map_id=11,
                    patch="16.20",
                    start_at=datetime.now(UTC),
                    duration_seconds=1800,
                    raw_hash=hashlib.sha256(match_id.encode()).hexdigest(),
                )
            )
            session.add(
                ParticipantModel(
                    match_id=match_id,
                    participant_id=1,
                    champion_id=9000,
                    team=100,
                    role="MIDDLE",
                    subject_hash="cc803-synthetic-test",
                    win=None,
                )
            )
            session.add(
                TimelineEventModel(
                    match_id=match_id,
                    source_index=1,
                    timestamp_ms=300000,
                    type="ITEM_PURCHASED",
                    payload={"participantId": 1, "itemId": 100010},
                )
            )
            session.add(
                MatchReconstructionModel(
                    match_id=match_id,
                    reconstruction_version=RECONSTRUCTION_ALGORITHM_VERSION,
                    catalog_id=point_catalog_id,
                    rules_provenance=[],
                )
            )
            await session.flush()
            session.add(
                DecisionStateModel(
                    id=state_id,
                    match_id=match_id,
                    participant_id=1,
                    event_source_index=1,
                    state_version=RECONSTRUCTION_ALGORITHM_VERSION,
                    snapshot={
                        "eventTimestampMs": 300000,
                        "inventory": {"items": {}, "trinketItemId": None},
                    },
                    features={},
                    quality={
                        "goldEstimate": {"value": 1000, "origin": "UNKNOWN", "ageMs": None},
                        "levelObservation": {
                            "value": None,
                            "ageMs": None,
                            "absentReason": "NO_PRIOR_FRAME",
                        },
                        "preDecisionQuality": {"uncertain": False, "reasons": []},
                        "empiricalRules": [],
                    },
                    schema_version="v1",
                    catalog_id=point_catalog_id,
                )
            )
            await session.flush()
            session.add(
                DecisionExtractionModel(
                    id=extraction_id,
                    match_id=match_id,
                    reconstruction_version=RECONSTRUCTION_ALGORITHM_VERSION,
                    selection_version="cc803_test_selection_v1",
                    feature_schema_version="feature_schema_v1",
                    scope_version="cc803_test_scope_v1",
                    classification_version="item_classification_v1",
                    curation_rules_version="curated_demo_v1",
                    catalog_id=point_catalog_id,
                    point_count=1,
                    eligible_count=1,
                )
            )
            session.add(
                DecisionPointModel(
                    id=decision_id,
                    extraction_id=extraction_id,
                    decision_state_id=state_id,
                    match_id=match_id,
                    participant_id=1,
                    event_source_index=1,
                    event_timestamp_ms=300000,
                    reconstruction_version=RECONSTRUCTION_ALGORITHM_VERSION,
                    selection_version="cc803_test_selection_v1",
                    feature_schema_version="feature_schema_v1",
                    scope_version="cc803_test_scope_v1",
                    classification_version="item_classification_v1",
                    curation_rules_version="curated_demo_v1",
                    catalog_id=point_catalog_id,
                    target_item_id=100010,
                    target_class="MAIN",
                    main_item_ordinal=1,
                    eligible=True,
                    reasons=[],
                    budget={"value": 1000, "origin": "UNKNOWN", "ageMs": None},
                    features={"values": {"self_champion_id": 9000}},
                )
            )

        configs = tmp_path / "configs"
        (configs / "ranking").mkdir(parents=True)
        (configs / "item-rules").mkdir()
        repository_root = Path(__file__).resolve().parents[2]
        if not (repository_root / "configs").is_dir():
            repository_root = Path(__file__).resolve().parents[3]
        for filename in ("rules-v1.yaml", "objective-v1.yaml"):
            shutil.copy2(
                repository_root / "configs" / "ranking" / filename, configs / "ranking" / filename
            )
        artifact = {
            "version": "popularity_16_19_v2",
            "gamePatch": "16.19",
            "datasetSha256": hashlib.sha256(b"cc803-r02-service-training").hexdigest(),
            "cells": {"9000|1": {"100010": 19}},
        }
        artifact_path = configs / "ranking" / "popularity-16.19-v2.json"
        artifact_bytes = (json.dumps(artifact, separators=(",", ":")) + "\n").encode()
        artifact_path.write_bytes(artifact_bytes)
        identity = {
            "gamePatch": source.game_patch,
            "dataDragonVersion": source.data_dragon_version,
            "sha256": source.sha256,
            "mappingVersion": source.mapping_version,
            "rawHash": source.raw_hash,
            "curationRulesVersion": source.curation_rules_version,
        }
        sidecar = configs / "ranking" / "popularity-16.19-v2.provenance.json"
        sidecar.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "artifactSha256": hashlib.sha256(artifact_bytes).hexdigest(),
                    "datasetSha256": artifact["datasetSha256"],
                    "sourceCatalogIdentity": identity,
                }
            ),
            encoding="utf-8",
        )
        (configs / "item-rules" / "16.20.json").write_text(
            json.dumps(
                {
                    "version": "cc803-test-rules-v1",
                    "gamePatch": "16.20",
                    "curationRulesVersion": "curated_demo_v1",
                    "excludedIds": [],
                    "exclusiveGroups": [],
                    "exclusiveCoverage": True,
                    "uniqueItemIds": True,
                    "highIdNamespaceExcluded": False,
                    "coveredItemIds": [100010],
                    "evidence": "fixture de integração sintética",
                }
            ),
            encoding="utf-8",
        )

        first, created = await application.create_analysis(session_factory, body, key, configs)
        assert created is True
        assert first["status"] == "AVAILABLE" and first["engine"] == "POPULARITY", (
            first["support"],
            first["candidates"],
        )
        assert first["versions"]["catalog"] == "16.20/16.20.1"
        assert first["versions"]["ranking"] == "popularity_16_19_v2 (herdado)"
        assert first["support"]["level"] == "LIMITED"
        assert first["support"]["reasonCodes"] == ["POPULARITY_INHERITED"]
        assert first["support"]["referenceDecisions"] == 19
        assert first["support"]["referenceSource"]["catalogId"] == str(source_id)
        assert [candidate["itemId"] for candidate in first["candidates"]] == [100010]

        # Remove a fonte de teste e corromper a ponte: chave antiga continua
        # respondendo do resultado persistido; uma chave nova falha fechada.
        async with unit_of_work(session_factory) as session:
            await session.execute(delete(ItemModel).where(ItemModel.catalog_id == source_id))
            await session.execute(
                delete(CatalogVersionModel).where(CatalogVersionModel.id == source_id)
            )
        changed_identity = {**identity, "sha256": hashlib.sha256(b"mismatch").hexdigest()}
        sidecar.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "artifactSha256": hashlib.sha256(artifact_bytes).hexdigest(),
                    "datasetSha256": artifact["datasetSha256"],
                    "sourceCatalogIdentity": changed_identity,
                }
            ),
            encoding="utf-8",
        )
        again, created_again = await application.create_analysis(
            session_factory, body, key, configs
        )
        assert created_again is False and again == first
        failed, failed_created = await application.create_analysis(
            session_factory, body, f"{key}-new", configs
        )
        assert failed_created is True
        assert failed["engine"] == "RULES"
        assert failed["support"]["reasonCodes"] == ["POPULARITY_SOURCE_CATALOG_NOT_FOUND"]
        async with session_factory() as session:
            stored = await session.get(AnalysisModel, uuid.UUID(first["analysisId"]))
        assert stored is not None and stored.result["support"]["referenceDecisions"] == 19
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(
                delete(AnalysisModel).where(AnalysisModel.decision_id == decision_id)
            )
            await session.execute(
                delete(DecisionPointModel).where(DecisionPointModel.id == decision_id)
            )
            await session.execute(
                delete(DecisionExtractionModel).where(DecisionExtractionModel.id == extraction_id)
            )
            await session.execute(
                delete(DecisionStateModel).where(DecisionStateModel.id == state_id)
            )
            await session.execute(
                delete(MatchReconstructionModel).where(
                    MatchReconstructionModel.match_id == match_id
                )
            )
            await session.execute(
                delete(TimelineEventModel).where(TimelineEventModel.match_id == match_id)
            )
            await session.execute(
                delete(ParticipantModel).where(ParticipantModel.match_id == match_id)
            )
            await session.execute(delete(MatchModel).where(MatchModel.match_id == match_id))
            await session.execute(
                delete(CatalogIdentityModel).where(
                    CatalogIdentityModel.game_patch == "16.20",
                    CatalogIdentityModel.data_dragon_version == "16.20.1",
                    CatalogIdentityModel.mapping_version == "cc803-service-map",
                )
            )
            for catalog_id in (source_id, point_catalog_id, canonical_id):
                await session.execute(delete(ItemModel).where(ItemModel.catalog_id == catalog_id))
                await session.execute(
                    delete(CatalogVersionModel).where(CatalogVersionModel.id == catalog_id)
                )
