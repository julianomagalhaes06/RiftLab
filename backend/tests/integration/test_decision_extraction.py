"""extract_decisions (CC-204) contra PostgreSQL real: pipeline
completo com o importador real (CC-201), o catálogo demo real (CC-202) e a
reconstrução v5 (CC-203) — sync demo -> import demo -> reconstruct -> extract.
A extração é só de banco: nenhuma fonte, nenhuma reconstrução, nenhuma rede."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from pathlib import Path

import pytest
from pipeline_helpers import (
    DEMO_1_MAPPING,
    import_demo_match,
    sync_demo_catalog,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from typer.testing import CliRunner

from riftlab.catalog.application import resolve_catalog_for_match, sync_catalog
from riftlab.catalog.classification import classify_catalog
from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping
from riftlab.catalog.infrastructure.demo_source import DemoCatalogSource
from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.cli.main import app
from riftlab.decisions import application as decisions_application
from riftlab.decisions import selection as decisions_selection
from riftlab.decisions.application import extract_decisions
from riftlab.decisions.domain import (
    DecisionConfigError,
    DecisionError,
    DecisionExtractionMarker,
    DecisionVersions,
    MatchContext,
    ParticipantView,
    StoredDecisionPoint,
)
from riftlab.decisions.repository import SqlAlchemyDecisionRepository
from riftlab.decisions.scope import DEMO_SCOPE
from riftlab.decisions.selection import evaluate_decision
from riftlab.ingestion.infrastructure.demo_source import DemoMatchSource
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import ConflictError, NotFoundError
from riftlab.timeline import application as timeline_application
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION, reconstruct_match
from riftlab.timeline.domain import DecisionState, TimelineEvent
from riftlab.timeline.repository import SqlAlchemyTimelineRepository

_NO_SCOPE_FILE = Path("/nonexistent/riftlab-scope.yaml")  # DEMO nunca lê este arquivo
_EMPTY_MAPPING = CatalogMapping(schema_version="v1", mapping_version="v1", entries={})
_RUNNER = CliRunner()


class _RealOriginSource:
    """Mesma partida demo, declarada como origem REAL: permite exercitar os
    gates de dado real (UNVALIDATED_*) e o recorte real sem partida real."""

    def __init__(self, inner: DemoMatchSource) -> None:
        self._inner = inner

    async def load_match(self, match_id: str):
        result = await self._inner.load_match(match_id)
        return dataclasses.replace(result, origin=SourceOrigin.COMPLETED_MATCH)


async def _prepare(session_factory, tmp_path, match_id: str, source=None) -> uuid.UUID | None:
    await sync_demo_catalog(session_factory)
    await import_demo_match(session_factory, tmp_path, match_id)
    result = await reconstruct_match(
        session_factory, DEMO_1_MAPPING, source or DemoMatchSource(), match_id
    )
    return result.catalog_id


async def _counts(session_factory, match_id: str) -> tuple[int, int]:
    async with unit_of_work(session_factory) as session:
        markers = await SqlAlchemyDecisionRepository(session).list_extractions(match_id)
        points = 0
        for marker in markers:
            points += len(await SqlAlchemyDecisionRepository(session).list_points(marker.id))
    return len(markers), points


async def _extract(session_factory, match_id: str, scope_path: Path = _NO_SCOPE_FILE):
    return await extract_decisions(session_factory, match_id, scope_path=scope_path)


def _codes(point: StoredDecisionPoint) -> dict[str, list[str]]:
    return {reason["code"]: reason["subReasons"] for reason in point.reasons}


# ---------------------------------------------------------------------------
# DEMO-0002 ponta a ponta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_demo_0002_end_to_end_persists_one_eligible_point_equal_to_the_pure_path(
    session_factory, tmp_path, _demo_match
) -> None:
    match_id = "DEMO-0002"
    catalog_id = await _prepare(session_factory, tmp_path, match_id)
    extraction = await _extract(session_factory, match_id)

    assert extraction.reused is False
    marker = extraction.marker
    assert (marker.point_count, marker.eligible_count) == (1, 1)
    [point] = extraction.points
    assert (point.target_item_id, point.target_class) == (100010, "MAIN")
    assert point.eligible and point.main_item_ordinal == 1 and point.reasons == []
    assert point.budget["codes"] == ["STALE_FRAME"]  # orçamento, não elegibilidade
    assert (point.budget["value"], point.budget["ageMs"]) == (500, 472_000)
    assert point.budget["immediatePurchaseAssertable"] is False
    assert point.versions.to_payload() == {
        "reconstructionVersion": RECONSTRUCTION_ALGORITHM_VERSION,
        "selectionVersion": "decision_selection_v1",
        "featureSchemaVersion": "feature_schema_v1",
        "scopeVersion": "demo_scope_v1",
        "catalogId": str(catalog_id),
        "curationRulesVersion": "curated_demo_v1",
        "classificationVersion": "item_classification_v1",
    }

    # persistido == devolvido == caminho puro (mesmas entradas lidas do banco)
    async with unit_of_work(session_factory) as session:
        stored = await SqlAlchemyDecisionRepository(session).list_points(marker.id)
        ingestion = SqlAlchemyIngestionRepository(session)
        catalog_repo = SqlAlchemyCatalogRepository(session)
        match = await ingestion.get_match(match_id)
        participants = tuple(
            ParticipantView.from_participant(p) for p in await ingestion.list_participants(match_id)
        )
        states = await SqlAlchemyTimelineRepository(session).list_decision_states(match_id, 1)
        row = await catalog_repo.get_catalog_version(catalog_id)
        items = {item.item_id: item for item in await catalog_repo.list_items(catalog_id)}
    assert match is not None and row is not None
    assert stored == extraction.points
    current = [s for s in states if s.state_version == RECONSTRUCTION_ALGORITHM_VERSION]
    table = classify_catalog(items, curation_rules_version=row.curation_rules_version)
    pure = [
        evaluate_decision(
            s,
            participants=participants,
            match=MatchContext.from_match(match),
            catalog=row,
            classification=table,
            scope=DEMO_SCOPE,
        )
        for s in current
    ]
    [pure_point] = [p for p in pure if p is not None]
    assert pure_point.features is not None
    assert point.features == pure_point.features.to_payload()
    assert point.decision_state_id == pure_point.state_id

    # decision_states não foi reescrita: continua com features == {}
    assert all(s.features == {} for s in current)


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_rerun_is_idempotent_and_never_touches_the_source_or_reconstruction(
    session_factory, tmp_path, _demo_match, monkeypatch
) -> None:
    match_id = "DEMO-0002"
    await _prepare(session_factory, tmp_path, match_id)
    first = await _extract(session_factory, match_id)
    before = await _counts(session_factory, match_id)

    async def _forbidden(*args, **kwargs):
        raise AssertionError("a extração não pode abrir a fonte nem reconstruir")

    monkeypatch.setattr(DemoMatchSource, "load_match", _forbidden)
    monkeypatch.setattr(timeline_application, "reconstruct_match", _forbidden)
    second = await _extract(session_factory, match_id)

    assert second.reused is True and second.marker.id == first.marker.id
    assert [p.id for p in second.points] == [p.id for p in first.points]
    assert await _counts(session_factory, match_id) == before == (1, 1)


@pytest.mark.parametrize("_demo_match", ["DEMO-0001"], indirect=True)
async def test_match_with_zero_points_is_marked_extracted_with_zero_points(
    session_factory, tmp_path, _demo_match
) -> None:
    """DEMO-0001 só compra um componente: nenhum ponto. A marca com contagem 0
    distingue "extraída com zero pontos" de "não extraída"."""
    match_id = "DEMO-0001"
    await _prepare(session_factory, tmp_path, match_id)
    first = await _extract(session_factory, match_id)
    assert (first.marker.point_count, first.marker.eligible_count) == (0, 0)
    assert first.points == []
    second = await _extract(session_factory, match_id)
    assert second.reused is True and second.marker.id == first.marker.id


# ---------------------------------------------------------------------------
# Pré-condições explícitas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("old_version", [4, 5])
@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_match_with_only_an_old_reconstruction_version_is_an_explicit_error(
    session_factory, tmp_path, _demo_match, old_version
) -> None:
    match_id = "DEMO-0002"
    await sync_demo_catalog(session_factory)
    await import_demo_match(session_factory, tmp_path, match_id)
    v4_state = DecisionState(
        id=uuid.uuid4(),
        match_id=match_id,
        participant_id=1,
        event_source_index=0,
        state_version=old_version,
        snapshot={"inventory": {"items": {}, "trinketItemId": None}, "purchasedItemId": 100001},
        features={},
        quality={"uncertain": False, "reasons": []},
        catalog_id=None,
    )
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyTimelineRepository(session)
        await repo.add_decision_state(v4_state)
        await repo.add_match_reconstruction(match_id, old_version, None)

    with pytest.raises(DecisionError) as excinfo:
        await _extract(session_factory, match_id)
    assert excinfo.value.code == "RECONSTRUCTION_REQUIRED"
    assert await _counts(session_factory, match_id) == (0, 0)  # nada gravado, nada reconstruído


async def test_unknown_match_is_not_found(session_factory) -> None:
    with pytest.raises(NotFoundError):
        await _extract(session_factory, "DEMO-NEVER-IMPORTED")


# ---------------------------------------------------------------------------
# REAL sintético: inelegível, motivos preservados, sem subject_hash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_real_origin_points_are_ineligible_with_reasons_and_no_subject_hash_anywhere(
    session_factory, tmp_path, _demo_match
) -> None:
    match_id = "DEMO-0002"
    await sync_demo_catalog(session_factory)
    await import_demo_match(session_factory, tmp_path, match_id)
    # a compra do item 100010 é desfeita por undo e refeita
    async with unit_of_work(session_factory) as session:
        timeline = SqlAlchemyTimelineRepository(session)
        undo = {"participantId": 1, "beforeId": 100010, "afterId": 0}
        await timeline.add_event(TimelineEvent(match_id, 4, 473_000, "ITEM_UNDO", undo))
        rebuy = {"participantId": 1, "itemId": 100010}
        await timeline.add_event(TimelineEvent(match_id, 5, 474_000, "ITEM_PURCHASED", rebuy))
    await reconstruct_match(
        session_factory, DEMO_1_MAPPING, _RealOriginSource(DemoMatchSource()), match_id
    )

    with pytest.raises(DecisionConfigError) as missing_scope:  # arquivo ausente: erro explícito
        await _extract(session_factory, match_id, tmp_path / "sem-recorte.yaml")
    assert missing_scope.value.code == "SCOPE_FILE_MISSING"
    assert await _counts(session_factory, match_id) == (0, 0)

    legacy_scope = tmp_path / "legacy-scope.yaml"
    legacy_scope.write_text(
        "schemaVersion: v1\nfrozenAt: '2026-09-14'\nqueue: 420\nmap: 11\n"
        "role: BOTTOM\nchampionIds: [145, 51, 81]\npatches:\n"
        "  - gamePatch: '16.16'\n  - gamePatch: '16.17'\n"
    )
    extraction = await _extract(session_factory, match_id, legacy_scope)
    assert extraction.marker.versions.scope_version == "scope_yaml:v1:2026-09-14"
    assert (extraction.marker.point_count, extraction.marker.eligible_count) == (2, 0)
    reverted, rebought = extraction.points  # ordem: event_timestamp_ms, source_index
    assert (reverted.event_source_index, rebought.event_source_index) == (3, 5)
    assert reverted.features is None and rebought.features is None  # inelegível: sem features
    assert _codes(reverted)["TARGET_REVERTED"] == ["REVERTED_BY_UNDO", "UNVALIDATED_UNDO_SEMANTICS"]
    assert "UNVALIDATED_UNDO_SEMANTICS" in _codes(rebought)["INVENTORY_UNCERTAIN"]
    for point in (reverted, rebought):  # recorte real: patch/campeão do demo fora do escopo
        assert _codes(point)["OUT_OF_SCOPE"] == ["PATCH_OUT_OF_SCOPE", "CHAMPION_OUT_OF_SCOPE"]
    summary = extraction.summary()
    assert summary["subReasonCounts"]["INVENTORY_UNCERTAIN:UNVALIDATED_UNDO_SEMANTICS"] == 1

    # varredura: nenhum subject_hash em nenhuma coluna/JSONB das tabelas novas
    async with unit_of_work(session_factory) as session:
        participants = await SqlAlchemyIngestionRepository(session).list_participants(match_id)
        rows: list[str] = []
        for table in ("decision_points", "decision_extractions"):
            result = await session.execute(
                text(f"SELECT row_to_json(t)::text FROM {table} t WHERE match_id = :m"),
                {"m": match_id},
            )
            rows.extend(row[0] for row in result.all())
        dump = "\n".join(rows)
    hashes = [p.subject_hash for p in participants]
    assert hashes and all(len(h) >= 16 for h in hashes)
    assert dump and not any(h in dump for h in hashes)
    assert "subject" not in dump.lower() and "puuid" not in dump.lower()


# ---------------------------------------------------------------------------
# Tupla de versões: NULL, coexistência, catálogo da marca, catálogo v4
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_duplicate_marker_or_point_with_null_version_columns_is_rejected(
    session_factory, tmp_path, _demo_match
) -> None:
    """Sem catálogo, classificação/curadoria/catálogo são NULL na tupla. Em
    UNIQUE comum, NULLs são distintos e permitiriam duplicatas; a restrição
    usa NULLS NOT DISTINCT."""
    match_id = "DEMO-0002"
    await import_demo_match(session_factory, tmp_path, match_id)
    result = await reconstruct_match(session_factory, _EMPTY_MAPPING, DemoMatchSource(), match_id)
    assert result.catalog_id is None
    extraction = await _extract(session_factory, match_id)
    versions = extraction.marker.versions
    assert (versions.classification_version, versions.curation_rules_version) == (None, None)
    assert versions.catalog_id is None
    assert (extraction.marker.point_count, extraction.marker.eligible_count) == (4, 0)
    assert all("MISSING_CATALOG" in _codes(p) for p in extraction.points)

    duplicate = DecisionExtractionMarker(uuid.uuid4(), match_id, versions, 0, 0)
    async with session_factory() as session:
        with pytest.raises(ConflictError):
            await SqlAlchemyDecisionRepository(session).add_extraction(duplicate)
    async with session_factory() as session:
        duplicate_point = dataclasses.replace(extraction.points[0], id=uuid.uuid4())
        await SqlAlchemyDecisionRepository(session).add_point(duplicate_point)
        with pytest.raises(IntegrityError):  # (extração, estado) é único
            await session.flush()
    assert await _counts(session_factory, match_id) == (1, 4)  # nada duplicado


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_new_version_tuple_coexists_with_the_old_one_and_preserves_it(
    session_factory, tmp_path, _demo_match, monkeypatch
) -> None:
    match_id = "DEMO-0002"
    await _prepare(session_factory, tmp_path, match_id)
    first = await _extract(session_factory, match_id)

    monkeypatch.setattr(decisions_selection, "DECISION_SELECTION_VERSION", "decision_selection_v2")
    second = await _extract(session_factory, match_id)

    assert second.reused is False and second.marker.id != first.marker.id
    assert second.marker.versions.selection_version == "decision_selection_v2"
    assert [p.id for p in second.points] != [p.id for p in first.points]  # linhas novas
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyDecisionRepository(session)
        markers = await repo.list_extractions(match_id)
        old_points = await repo.list_points(first.marker.id)
    assert {m.id for m in markers} == {first.marker.id, second.marker.id}
    assert old_points == first.points  # a tupla antiga ficou intacta
    # a tupla antiga continua sendo reaproveitada quando a versão volta
    monkeypatch.undo()
    again = await _extract(session_factory, match_id)
    assert again.reused is True and again.marker.id == first.marker.id


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_extraction_uses_the_catalog_of_the_reconstruction_marker_not_the_canonical_one(
    session_factory, tmp_path, _demo_match
) -> None:
    match_id = "DEMO-0002"
    marker_catalog_id = await _prepare(session_factory, tmp_path, match_id)
    assert marker_catalog_id is not None

    # outro conteúdo sincronizado para a MESMA identidade vira o canônico atual
    demo_result = await DemoCatalogSource().load_catalog("DEMO-1")

    class _OtherContent:
        async def load_catalog(self, game_patch: str):
            return dataclasses.replace(demo_result, raw_hash="e" * 64)

    other = await sync_catalog(session_factory, _OtherContent(), "DEMO-1")
    canonical = await resolve_catalog_for_match(session_factory, DEMO_1_MAPPING, "DEMO-1")
    assert canonical is not None and canonical.id == other.id != marker_catalog_id

    extraction = await _extract(session_factory, match_id)
    assert extraction.marker.versions.catalog_id == marker_catalog_id  # o da MARCA
    await sync_demo_catalog(session_factory)  # restaura o canônico para os outros testes


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_legacy_v4_curation_catalog_classifies_everything_as_unclassified(
    session_factory, tmp_path, _demo_match
) -> None:
    match_id = "DEMO-0002"
    demo_result = await DemoCatalogSource().load_catalog("DEMO-1")

    class _LegacyCuration:
        async def load_catalog(self, game_patch: str):
            return dataclasses.replace(demo_result, curation_rules_version="data_dragon_v4")

    legacy = await sync_catalog(session_factory, _LegacyCuration(), "DEMO-1")
    try:
        await import_demo_match(session_factory, tmp_path, match_id)
        result = await reconstruct_match(
            session_factory, DEMO_1_MAPPING, DemoMatchSource(), match_id
        )
        assert result.catalog_id == legacy.id
        extraction = await _extract(session_factory, match_id)
    finally:
        await sync_demo_catalog(session_factory)  # restaura o canônico
    versions = extraction.marker.versions
    assert versions.curation_rules_version == "data_dragon_v4"
    assert extraction.marker.eligible_count == 0
    assert (
        extraction.marker.point_count == 4
    )  # todo alvo é inclassificável: listado, não descartado
    for point in extraction.points:
        assert point.target_class == "UNCLASSIFIED" and point.features is None
        assert _codes(point)["TARGET_NOT_CLASSIFIABLE"] == [
            "UNCLASSIFIED",
            "CLASSIFICATION_UNSUPPORTED_CURATION",
        ]


# ---------------------------------------------------------------------------
# Concorrência
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_concurrent_markers_for_the_same_tuple_only_one_wins(
    session_factory, tmp_path, _demo_match
) -> None:
    """Concorrência REAL (duas transações independentes; a segunda é enviada
    ao PostgreSQL enquanto a primeira ainda não commitou), mesmo padrão de
    test_catalog_sync.py: a perdedora recebe ConflictError, sem pontos parciais."""
    match_id = "DEMO-0002"
    catalog_id = await _prepare(session_factory, tmp_path, match_id)
    versions = DecisionVersions(
        RECONSTRUCTION_ALGORITHM_VERSION,
        "decision_selection_v1",
        "feature_schema_v1",
        "demo_scope_v1",
        catalog_id,
        "curated_demo_v1",
        "item_classification_v1",
    )
    marker_a = DecisionExtractionMarker(uuid.uuid4(), match_id, versions, 0, 0)
    marker_b = DecisionExtractionMarker(uuid.uuid4(), match_id, versions, 0, 0)
    a_inserted, b_attempted = asyncio.Event(), asyncio.Event()
    results: dict[str, str] = {}

    async def txn_a() -> None:
        async with session_factory() as session:
            await SqlAlchemyDecisionRepository(session).add_extraction(marker_a)
            a_inserted.set()
            await asyncio.wait_for(b_attempted.wait(), timeout=5)
            await session.commit()
        results["a"] = "committed"

    async def txn_b() -> None:
        await asyncio.wait_for(a_inserted.wait(), timeout=5)
        async with session_factory() as session:
            insert = asyncio.ensure_future(
                SqlAlchemyDecisionRepository(session).add_extraction(marker_b)
            )
            await asyncio.sleep(0.3)
            b_attempted.set()
            with pytest.raises(ConflictError):
                await asyncio.wait_for(insert, timeout=5)
        results["b"] = "conflict"

    await asyncio.wait_for(asyncio.gather(txn_a(), txn_b()), timeout=15)
    assert results == {"a": "committed", "b": "conflict"}
    async with unit_of_work(session_factory) as session:
        markers = await SqlAlchemyDecisionRepository(session).list_extractions(match_id)
    assert [m.id for m in markers] == [marker_a.id]


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_concurrent_extractions_never_leave_two_markers_or_partial_points(
    session_factory, tmp_path, _demo_match
) -> None:
    match_id = "DEMO-0002"
    await _prepare(session_factory, tmp_path, match_id)
    outcomes = await asyncio.gather(
        *(_extract(session_factory, match_id) for _ in range(4)), return_exceptions=True
    )
    assert any(not isinstance(o, BaseException) for o in outcomes)  # ao menos uma concluiu
    assert all(isinstance(o, ConflictError) for o in outcomes if isinstance(o, BaseException))
    winners = {o.marker.id for o in outcomes if not isinstance(o, BaseException)}
    assert len(winners) == 1  # todas as que concluíram enxergam a mesma marca
    markers, points = await _counts(session_factory, match_id)
    assert (markers, points) == (1, 1)  # uma marca; pontos completos (== point_count)


# ---------------------------------------------------------------------------
# Garantias de esquema e CLI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_schema_guarantees_immutability_nulls_not_distinct_and_pagination_index(
    session_factory, tmp_path, _demo_match
) -> None:
    match_id = "DEMO-0002"
    await _prepare(session_factory, tmp_path, match_id)
    extraction = await _extract(session_factory, match_id)
    [point] = extraction.points

    async with unit_of_work(session_factory) as session:
        nulls_not_distinct = (
            await session.execute(
                text(
                    "SELECT i.indnullsnotdistinct FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname = 'uq_decision_extractions_version_tuple'"
                )
            )
        ).scalar_one()
        index_columns = (
            await session.execute(
                text(
                    "SELECT array_agg(a.attname::text ORDER BY k.ord) "
                    "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                    "CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) "
                    "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
                    "WHERE c.relname = 'ix_decision_points_pagination'"
                )
            )
        ).scalar_one()
    assert nulls_not_distinct is True
    assert index_columns == [
        "match_id",
        "participant_id",
        "extraction_id",
        "event_timestamp_ms",
        "event_source_index",
        "id",
    ]

    for table in ("decision_points", "decision_extractions"):  # linhas imutáveis
        async with session_factory() as session:
            with pytest.raises(DBAPIError):
                await session.execute(
                    text(f"UPDATE {table} SET created_at = now() WHERE match_id = :m"),
                    {"m": match_id},
                )

    async with session_factory() as session:  # elegível exige features (e vice-versa)
        broken = dataclasses.replace(point, id=uuid.uuid4(), features=None)
        await SqlAlchemyDecisionRepository(session).add_point(broken)
        with pytest.raises(IntegrityError):
            await session.flush()
    async with session_factory() as session:  # identidade completa do estado de origem
        forged = dataclasses.replace(point, id=uuid.uuid4(), participant_id=6)
        await SqlAlchemyDecisionRepository(session).add_point(forged)
        with pytest.raises(IntegrityError):
            await session.flush()
    assert await _counts(session_factory, match_id) == (1, 1)


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_cli_extract_summarizes_without_personal_data_and_is_idempotent(
    session_factory, tmp_path, _demo_match
) -> None:
    match_id = "DEMO-0002"
    await _prepare(session_factory, tmp_path, match_id)

    def _invoke(*args: str):
        return _RUNNER.invoke(app, ["decisions", "extract", *args])

    first = await asyncio.to_thread(_invoke, "--match-id", match_id)  # asyncio.run em outra thread
    assert first.exit_code == 0, first.output
    summary = json.loads(first.output)
    assert summary["matchId"] == match_id and summary["reused"] is False
    assert (summary["pointCount"], summary["eligibleCount"]) == (1, 1)
    assert summary["versions"]["scopeVersion"] == "demo_scope_v1"
    assert summary["reasonCounts"] == {}
    async with unit_of_work(session_factory) as session:
        hashes = [
            p.subject_hash
            for p in await SqlAlchemyIngestionRepository(session).list_participants(match_id)
        ]
    assert not any(h in first.output for h in hashes)

    second = await asyncio.to_thread(_invoke, "--match-id", match_id)
    assert second.exit_code == 0 and json.loads(second.output)["reused"] is True

    unknown = await asyncio.to_thread(_invoke, "--match-id", "DEMO-NEVER-IMPORTED")
    assert unknown.exit_code == 1 and "BLOCKED: NotFoundError" in unknown.output


async def test_decisions_application_never_receives_a_source_or_transport() -> None:
    """A assinatura é a garantia: nenhum parâmetro de fonte, raw ou rede."""
    import inspect

    parameters = set(inspect.signature(decisions_application.extract_decisions).parameters)
    assert parameters == {"session_factory", "match_id", "scope_path"}


# ---------------------------------------------------------------------------
# DEMO-0008: forma real (destroys dos componentes ANTES da compra composta)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_demo_0008_real_form_end_to_end_is_eligible_with_components_in_the_snapshot(
    session_factory, tmp_path, _demo_match
) -> None:
    """Sync -> import -> reconstruct v6 -> extract, na forma real do corpus: os
    componentes voltam ao snapshot da compra (transação), a pré-decisão fica
    limpa mesmo com 3 destroys no mesmo timestamp, e o orçamento tem frame
    fresco (20 s). Antes da v6 esta partida daria 0 pontos elegíveis."""
    match_id = "DEMO-0008"
    await _prepare(session_factory, tmp_path, match_id)
    extraction = await _extract(session_factory, match_id)

    assert (extraction.marker.point_count, extraction.marker.eligible_count) == (1, 1)
    [point] = extraction.points
    assert (point.target_item_id, point.target_class) == (100010, "MAIN")
    assert point.eligible and point.reasons == [] and point.main_item_ordinal == 1
    assert point.features is not None
    values = point.features["values"]
    assert values["inventory_items"] == [[100001, 2], [100002, 1]]  # a receita do alvo, antes dele
    assert values["main_item_ordinal"] == 1
    assert (values["level"], values["gold_estimate"]) == (7, 640)
    assert point.budget["codes"] == [] and point.budget["immediatePurchaseAssertable"] is True
    assert (point.budget["value"], point.budget["ageMs"]) == (640, 20_000)

    async with unit_of_work(session_factory) as session:
        states = await SqlAlchemyTimelineRepository(session).list_decision_states(match_id, 1)
    current = [s for s in states if s.state_version == RECONSTRUCTION_ALGORITHM_VERSION]
    target = next(s for s in current if s.snapshot["purchasedItemId"] == 100010)
    assert target.quality["uncertain"] is False  # DEMO: destroys reivindicados sem marca
    assert target.quality["preDecisionQuality"] == {"uncertain": False, "reasons": []}
    assert target.snapshot["inventory"]["items"] == {"100001": 2, "100002": 1}
    claimed = target.quality["destroyOrderReconciliationHeuristic"]["claimedDestroySourceIndexes"]
    assert sorted(claimed) == [3, 4, 5]


@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_demo_0008_in_real_origin_is_blocked_only_by_scope_never_by_inventory(
    session_factory, tmp_path, _demo_match
) -> None:
    """Mesma partida declarada de origem REAL, recorte real: o único bloqueio é
    o recorte (o demo está fora de 16.16/16.17). O gate REAL de reconciliação
    (UNVALIDATED_DESTROY_ORDER_RECONCILIATION) afeta as decisões SEGUINTES, não
    a própria: nenhum INVENTORY_UNCERTAIN."""
    match_id = "DEMO-0008"
    await _prepare(session_factory, tmp_path, match_id, source=_RealOriginSource(DemoMatchSource()))
    legacy_scope = tmp_path / "legacy-scope.yaml"
    legacy_scope.write_text(
        "schemaVersion: v1\nfrozenAt: '2026-09-14'\nqueue: 420\nmap: 11\n"
        "role: BOTTOM\nchampionIds: [145, 51, 81]\npatches:\n"
        "  - gamePatch: '16.16'\n  - gamePatch: '16.17'\n"
    )
    extraction = await _extract(session_factory, match_id, legacy_scope)
    [point] = extraction.points
    assert set(_codes(point)) == {"OUT_OF_SCOPE"}
    assert _codes(point)["OUT_OF_SCOPE"] == ["PATCH_OUT_OF_SCOPE", "CHAMPION_OUT_OF_SCOPE"]
    assert point.features is None
