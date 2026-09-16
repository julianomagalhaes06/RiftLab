"""Testes de integração de persistência contra PostgreSQL real (nunca SQLite).
Cada teste cria seus próprios dados sintéticos mínimos, sem depender de fixtures
reais do CC-104."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.ingestion.domain import ImportJob, ImportJobState, ImportMode, Match, Participant
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import ConflictError
from riftlab.timeline.domain import DecisionState, TimelineEvent
from riftlab.timeline.repository import SqlAlchemyTimelineRepository


def _fake_sha256() -> str:
    """CatalogVersion.sha256 é identidade de conteúdo com índice único real
    (CC-202) — nunca um placeholder fixo repetido entre testes, senão uma
    reexecução da suíte contra o mesmo Postgres persistente colide consigo
    mesma (mesma lição de higiene já aplicada em CC-201)."""
    return uuid.uuid4().hex + uuid.uuid4().hex


def _match(match_id: str = "BR1_1000000001", **overrides: object) -> Match:
    defaults: dict[str, object] = {
        "match_id": match_id,
        "queue": 420,
        "map_id": 11,
        "patch": "14.18",
        "start_at": datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC),
        "duration_seconds": 1800,
        "raw_hash": "hash-" + match_id,
    }
    defaults.update(overrides)
    return Match(**defaults)  # type: ignore[arg-type]


def _participant(match_id: str, participant_id: int = 1, **overrides: object) -> Participant:
    defaults: dict[str, object] = {
        "match_id": match_id,
        "participant_id": participant_id,
        "champion_id": 99,
        "team": 100,
        "subject_hash": f"subject-{match_id}-{participant_id}",
    }
    defaults.update(overrides)
    return Participant(**defaults)  # type: ignore[arg-type]


def _event(match_id: str, source_index: int, **overrides: object) -> TimelineEvent:
    defaults: dict[str, object] = {
        "match_id": match_id,
        "source_index": source_index,
        "timestamp_ms": 60_000 + source_index,
        "type": "ITEM_PURCHASED",
        "payload": {"itemId": 1001},
    }
    defaults.update(overrides)
    return TimelineEvent(**defaults)  # type: ignore[arg-type]


async def _seed_match_participant_event(session, match_id: str) -> None:
    ingestion = SqlAlchemyIngestionRepository(session)
    timeline = SqlAlchemyTimelineRepository(session)
    await ingestion.add_match(_match(match_id))
    await ingestion.add_participant(_participant(match_id))
    await timeline.add_event(_event(match_id, source_index=0))
    # Sem relationship() do ORM entre os módulos (por design: domínio não importa
    # SQLAlchemy), o unit-of-work não tem como inferir a ordem de FK entre
    # mappers não relacionados — flush explícito garante que match/participant/
    # event existam antes de qualquer decision_state ser adicionado na mesma
    # transação por quem chamar este helper em seguida.
    await session.flush()


class TestCatalogRoundTrip:
    async def test_catalog_version_and_item_round_trip(self, session_factory):
        catalog_id = uuid.uuid4()
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyCatalogRepository(session)
            await repo.add_catalog_version(
                CatalogVersion(
                    id=catalog_id,
                    game_patch="14.18",
                    data_dragon_version="14.18.1",
                    sha256=_fake_sha256(),
                    mapping_version="v1",
                    raw_hash=_fake_sha256(),
                    curation_rules_version="v1",
                )
            )
            await repo.add_item(
                Item(
                    catalog_id=catalog_id,
                    item_id=1001,
                    price=1300,
                    attributes={"tags": ["damage"]},
                )
            )

        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyCatalogRepository(session)
            stored_catalog = await repo.get_catalog_version(catalog_id)
            stored_item = await repo.get_item(catalog_id, 1001)

        assert stored_catalog is not None
        assert stored_catalog.game_patch == "14.18"
        assert stored_item is not None
        assert stored_item.price == 1300
        assert stored_item.attributes == {"tags": ["damage"]}

    async def test_same_item_id_allowed_in_two_catalogs(self, session_factory):
        catalog_a, catalog_b = uuid.uuid4(), uuid.uuid4()
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyCatalogRepository(session)
            for catalog_id, patch in ((catalog_a, "14.17"), (catalog_b, "14.18")):
                await repo.add_catalog_version(
                    CatalogVersion(
                        id=catalog_id,
                        game_patch=patch,
                        data_dragon_version=patch + ".1",
                        sha256=_fake_sha256(),
                        mapping_version="v1",
                        raw_hash=_fake_sha256(),
                        curation_rules_version="v1",
                    )
                )
                await repo.add_item(Item(catalog_id=catalog_id, item_id=2003, price=800))

        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyCatalogRepository(session)
            item_a = await repo.get_item(catalog_a, 2003)
            item_b = await repo.get_item(catalog_b, 2003)
        assert item_a is not None and item_b is not None
        assert item_a.catalog_id != item_b.catalog_id

    async def test_negative_price_rejected(self, session_factory):
        catalog_id = uuid.uuid4()
        with pytest.raises(IntegrityError):
            async with unit_of_work(session_factory) as session:
                repo = SqlAlchemyCatalogRepository(session)
                await repo.add_catalog_version(
                    CatalogVersion(
                        id=catalog_id,
                        game_patch="14.18",
                        data_dragon_version="14.18.1",
                        sha256=_fake_sha256(),
                        mapping_version="v1",
                        raw_hash=_fake_sha256(),
                        curation_rules_version="v1",
                    )
                )
                await repo.add_item(Item(catalog_id=catalog_id, item_id=1, price=-1))


class TestMatchParticipantEvent:
    async def test_round_trip(self, session_factory):
        match_id = f"BR1_{uuid.uuid4().int % 10**10}"
        async with unit_of_work(session_factory) as session:
            await _seed_match_participant_event(session, match_id)

        async with unit_of_work(session_factory) as session:
            ingestion = SqlAlchemyIngestionRepository(session)
            timeline = SqlAlchemyTimelineRepository(session)
            match = await ingestion.get_match(match_id)
            participant = await ingestion.get_participant(match_id, 1)
            event = await timeline.get_event(match_id, 0)

        assert match is not None and match.start_at.tzinfo is not None
        assert participant is not None
        assert event is not None and event.type == "ITEM_PURCHASED"

    async def test_start_at_round_trips_timezone_aware(self, session_factory):
        match_id = f"BR1_{uuid.uuid4().int % 10**10}"
        moment = datetime(2026, 3, 5, 23, 59, 59, tzinfo=UTC)
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            await repo.add_match(_match(match_id, start_at=moment))

        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            stored = await repo.get_match(match_id)
        assert stored is not None
        assert stored.start_at.tzinfo is not None
        assert stored.start_at.astimezone(UTC) == moment

    async def test_negative_duration_rejected(self, session_factory):
        match_id = f"BR1_{uuid.uuid4().int % 10**10}"
        with pytest.raises(IntegrityError):
            async with unit_of_work(session_factory) as session:
                repo = SqlAlchemyIngestionRepository(session)
                await repo.add_match(_match(match_id, duration_seconds=-1))

    async def test_participant_orphan_without_match_rejected(self, session_factory):
        match_id = f"BR1_{uuid.uuid4().int % 10**10}"
        with pytest.raises(IntegrityError):
            async with unit_of_work(session_factory) as session:
                repo = SqlAlchemyIngestionRepository(session)
                await repo.add_participant(_participant(match_id))


class TestTimelineEvents:
    async def test_same_timestamp_different_source_index_allowed(self, session_factory):
        match_id = f"BR1_{uuid.uuid4().int % 10**10}"
        async with unit_of_work(session_factory) as session:
            ingestion = SqlAlchemyIngestionRepository(session)
            timeline = SqlAlchemyTimelineRepository(session)
            await ingestion.add_match(_match(match_id))
            await timeline.add_event(_event(match_id, source_index=0, timestamp_ms=60_000))
            await timeline.add_event(_event(match_id, source_index=1, timestamp_ms=60_000))

        async with unit_of_work(session_factory) as session:
            timeline = SqlAlchemyTimelineRepository(session)
            events = await timeline.list_events(match_id)
        assert len(events) == 2
        assert {e.source_index for e in events} == {0, 1}

    async def test_duplicate_source_index_rejected(self, session_factory):
        match_id = f"BR1_{uuid.uuid4().int % 10**10}"
        with pytest.raises(IntegrityError):
            async with unit_of_work(session_factory) as session:
                ingestion = SqlAlchemyIngestionRepository(session)
                timeline = SqlAlchemyTimelineRepository(session)
                await ingestion.add_match(_match(match_id))
                await timeline.add_event(_event(match_id, source_index=0))
                await timeline.add_event(_event(match_id, source_index=0))

    async def test_negative_timestamp_rejected(self, session_factory):
        match_id = f"BR1_{uuid.uuid4().int % 10**10}"
        with pytest.raises(IntegrityError):
            async with unit_of_work(session_factory) as session:
                ingestion = SqlAlchemyIngestionRepository(session)
                timeline = SqlAlchemyTimelineRepository(session)
                await ingestion.add_match(_match(match_id))
                await timeline.add_event(_event(match_id, source_index=0, timestamp_ms=-1))


class TestDecisionStates:
    async def test_round_trip_with_masked_absence_and_no_catalog(self, session_factory):
        match_id = f"BR1_{uuid.uuid4().int % 10**10}"
        state_id = uuid.uuid4()
        async with unit_of_work(session_factory) as session:
            await _seed_match_participant_event(session, match_id)
            timeline = SqlAlchemyTimelineRepository(session)
            await timeline.add_decision_state(
                DecisionState(
                    id=state_id,
                    match_id=match_id,
                    participant_id=1,
                    event_source_index=0,
                    snapshot={"gold": None},
                    features={"remaining_cost": None, "has_component": True},
                    quality={"budgetSource": "UNKNOWN"},
                    catalog_id=None,
                )
            )

        async with unit_of_work(session_factory) as session:
            timeline = SqlAlchemyTimelineRepository(session)
            stored = await timeline.get_decision_state(state_id)
        assert stored is not None
        assert stored.catalog_id is None
        assert stored.features["remaining_cost"] is None
        assert stored.features["has_component"] is True

    async def test_rejects_participant_from_other_match(self, session_factory):
        match_a = f"BR1_{uuid.uuid4().int % 10**10}"
        match_b = f"BR1_{uuid.uuid4().int % 10**10}"
        async with unit_of_work(session_factory) as session:
            # match_a tem participant_id=1; match_b tem participant_id=2 (distinto)
            await _seed_match_participant_event(session, match_a)
            ingestion = SqlAlchemyIngestionRepository(session)
            timeline = SqlAlchemyTimelineRepository(session)
            await ingestion.add_match(_match(match_b))
            await ingestion.add_participant(_participant(match_b, participant_id=2))
            await timeline.add_event(_event(match_b, source_index=0))

        with pytest.raises(IntegrityError):
            async with unit_of_work(session_factory) as session:
                timeline = SqlAlchemyTimelineRepository(session)
                await timeline.add_decision_state(
                    DecisionState(
                        id=uuid.uuid4(),
                        match_id=match_a,
                        participant_id=2,  # só existe em match_b, não em match_a
                        event_source_index=0,  # existe em match_a
                    )
                )

    async def test_rejects_event_from_other_match(self, session_factory):
        match_a = f"BR1_{uuid.uuid4().int % 10**10}"
        match_b = f"BR1_{uuid.uuid4().int % 10**10}"
        async with unit_of_work(session_factory) as session:
            # match_a tem source_index=0; match_b tem source_index=5 (distinto)
            await _seed_match_participant_event(session, match_a)
            ingestion = SqlAlchemyIngestionRepository(session)
            timeline = SqlAlchemyTimelineRepository(session)
            await ingestion.add_match(_match(match_b))
            await ingestion.add_participant(_participant(match_b, participant_id=1))
            await timeline.add_event(_event(match_b, source_index=5))

        with pytest.raises(IntegrityError):
            async with unit_of_work(session_factory) as session:
                timeline = SqlAlchemyTimelineRepository(session)
                await timeline.add_decision_state(
                    DecisionState(
                        id=uuid.uuid4(),
                        match_id=match_a,
                        participant_id=1,  # existe em match_a
                        event_source_index=5,  # só existe em match_b
                    )
                )


async def _consume_queued_job(session_factory, job_id: uuid.UUID) -> None:
    """CC-201: reserve_next_job (worker) escaneia a tabela inteira; um job
    QUEUED deixado por um teste de persistência pura ficaria elegível para
    qualquer teste posterior que reserve trabalho (test_import_job_lease.py,
    test_worker_processing.py). Finaliza aqui para não vazar entre arquivos —
    RUNNING/RETRY_WAIT criados diretamente (sem lease_until/retry_at) nunca
    são elegíveis (comparação com NULL nunca bate em SQL), não precisam
    disso."""
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        reserved = await repo.reserve_next_job(datetime.now(UTC))
        assert reserved is not None and reserved.id == job_id
        await repo.mark_import_failed(reserved.id, reserved.lease_generation, "TEST_CLEANUP")


class TestImportJobs:
    async def test_job_created_before_match_exists(self, session_factory):
        requested_match_id = f"BR1_{uuid.uuid4().int % 10**10}"
        job_id = uuid.uuid4()
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            await repo.create_import_job(
                ImportJob(
                    id=job_id,
                    requested_match_id=requested_match_id,
                    mode=ImportMode.DEMO,
                    state=ImportJobState.QUEUED,
                )
            )
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            match = await repo.get_match(requested_match_id)
        assert match is None  # job existe sem que a partida exista ainda

        await _consume_queued_job(session_factory, job_id)

    @pytest.mark.parametrize(
        "active_state",
        [ImportJobState.QUEUED, ImportJobState.RUNNING, ImportJobState.RETRY_WAIT],
    )
    async def test_duplicate_active_job_rejected_for_each_active_state(
        self, session_factory, active_state
    ):
        requested_match_id = f"BR1_{uuid.uuid4().int % 10**10}"
        existing_id = uuid.uuid4()
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            await repo.create_import_job(
                ImportJob(
                    id=existing_id,
                    requested_match_id=requested_match_id,
                    mode=ImportMode.DEMO,
                    state=active_state,
                )
            )

        with pytest.raises(ConflictError):
            async with unit_of_work(session_factory) as session:
                repo = SqlAlchemyIngestionRepository(session)
                await repo.create_import_job(
                    ImportJob(
                        id=uuid.uuid4(),
                        requested_match_id=requested_match_id,
                        mode=ImportMode.DEMO,
                        state=ImportJobState.QUEUED,
                    )
                )

        if active_state == ImportJobState.QUEUED:
            # RUNNING/RETRY_WAIT criados aqui sem lease_until/retry_at nunca
            # ficam elegíveis para reserve_next_job (NULL nunca bate em
            # comparação SQL) — só QUEUED precisa de limpeza explícita.
            await _consume_queued_job(session_factory, existing_id)

    async def test_terminal_state_frees_new_attempt_and_preserves_history(self, session_factory):
        requested_match_id = f"BR1_{uuid.uuid4().int % 10**10}"
        first_id = uuid.uuid4()
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            await repo.create_import_job(
                ImportJob(
                    id=first_id,
                    requested_match_id=requested_match_id,
                    mode=ImportMode.DEMO,
                    state=ImportJobState.FAILED,
                    attempts=1,
                    error_code="UPSTREAM_UNAVAILABLE",
                )
            )
            second_id = uuid.uuid4()
            await repo.create_import_job(
                ImportJob(
                    id=second_id,
                    requested_match_id=requested_match_id,
                    mode=ImportMode.DEMO,
                    state=ImportJobState.QUEUED,
                )
            )

        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            first = await repo.get_import_job(first_id)
            second = await repo.get_import_job(second_id)
        assert first is not None and first.state == ImportJobState.FAILED
        assert second is not None and second.state == ImportJobState.QUEUED

        await _consume_queued_job(session_factory, second_id)

    async def test_distinct_modes_are_separate_active_identity(self, session_factory):
        requested_match_id = f"BR1_{uuid.uuid4().int % 10**10}"
        demo_id = uuid.uuid4()
        riot_id = uuid.uuid4()
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            await repo.create_import_job(
                ImportJob(
                    id=demo_id,
                    requested_match_id=requested_match_id,
                    mode=ImportMode.DEMO,
                    state=ImportJobState.QUEUED,
                )
            )
            await repo.create_import_job(
                ImportJob(
                    id=riot_id,
                    requested_match_id=requested_match_id,
                    mode=ImportMode.RIOT,
                    state=ImportJobState.QUEUED,
                )
            )

        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            demo_jobs = await repo.list_active_import_jobs(requested_match_id, ImportMode.DEMO)
            riot_jobs = await repo.list_active_import_jobs(requested_match_id, ImportMode.RIOT)
        assert len(demo_jobs) == 1
        assert len(riot_jobs) == 1

        # RIOT tambem fica QUEUED (elegivel) — limpa sem deixar o worker
        # tentar rede real por cima deste job sintetico em teste algum
        # posterior.
        await _consume_queued_job(session_factory, demo_id)
        await _consume_queued_job(session_factory, riot_id)


class TestRollback:
    async def test_failed_transaction_leaves_no_partial_rows(self, session_factory):
        catalog_id = uuid.uuid4()
        orphan_match_id = f"BR1_{uuid.uuid4().int % 10**10}"

        with pytest.raises(IntegrityError):
            async with unit_of_work(session_factory) as session:
                catalog_repo = SqlAlchemyCatalogRepository(session)
                await catalog_repo.add_catalog_version(
                    CatalogVersion(
                        id=catalog_id,
                        game_patch="14.18",
                        data_dragon_version="14.18.1",
                        sha256=_fake_sha256(),
                        mapping_version="v1",
                        raw_hash=_fake_sha256(),
                        curation_rules_version="v1",
                    )
                )
                ingestion_repo = SqlAlchemyIngestionRepository(session)
                # participante sem partida correspondente: viola FK no commit
                await ingestion_repo.add_participant(_participant(orphan_match_id))

        # sessao/nova UoW segue utilizavel e nenhuma linha parcial ficou gravada
        async with unit_of_work(session_factory) as session:
            catalog_repo = SqlAlchemyCatalogRepository(session)
            stored = await catalog_repo.get_catalog_version(catalog_id)
        assert stored is None


class TestIndexes:
    async def test_expected_indexes_exist(self, session_factory):
        expected = {
            "ux_import_jobs_active_identity",
            "ix_import_jobs_state_retry_at",
            "ix_timeline_events_match_ts",
            "ix_decision_states_match_participant",
            "uq_catalog_versions_sha256",
            "ux_catalog_versions_canonical_identity",
        }
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            )
            names = {row[0] for row in result}
        assert expected <= names
