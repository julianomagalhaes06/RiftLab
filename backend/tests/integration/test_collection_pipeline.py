"""Coleta -> raw local -> importação -> preparação -> relatórios (CC-206) contra
PostgreSQL real, com servidor Riot FALSO (nenhuma rede, nenhuma chave real).

Prova, no banco, o que o card exige: retomada sem duplicar, crash entre o fetch e a
persistência e durante a preparação, importação repetida idempotente, 401 no meio e
retomada, tetos e filtros com contagem, e que NENHUMA partida parcial fica analisável.
A importação daqui é SEMPRE a partir do raw local: o cliente HTTP do worker é proibido."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pipeline_helpers import (
    StubCatalogSource,
    purge_open_preparations,
    reset_match,
    stub_catalog_factory,
    unique_patch,
    write_prepare_configs,
)
from sqlalchemy import func, select, update

from riftlab.api.app import create_app
from riftlab.collection.collector import CollectionCeilings, StopReason
from riftlab.collection.filters import MatchFilterConfig
from riftlab.collection.importing import drain_local, enqueue_collected_imports
from riftlab.collection.report import build_collection_report
from riftlab.config import Settings
from riftlab.decisions.models import DecisionExtractionModel, DecisionPointModel
from riftlab.ingestion import application as ingestion_application
from riftlab.ingestion.application import request_import
from riftlab.ingestion.domain import ImportMode
from riftlab.ingestion.infrastructure.subject_hash import compute_real_subject_hash
from riftlab.ingestion.models import (
    ImportJobModel,
    MatchModel,
    ParticipantModel,
    RiotRateLimitModel,
)
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.preparation import application as preparation
from riftlab.reports.diversity import build_diversity_report, render_diversity_report
from riftlab.reports.integrity import check_integrity, render_integrity_report
from riftlab.reports.reconstruction import (
    build_reconstruction_report,
    render_reconstruction_report,
)
from riftlab.shared.db import unit_of_work
from riftlab.timeline.models import MatchReconstructionModel, TimelineEventModel
from support.collection_harness import (
    SEED_A_PUUID,
    Harness,
    one_seed,
    two_seeds,
)
from support.riot_stub import HASH_SECRET, real_pair

PURCHASES = ((120_000, 3006),)


@pytest_asyncio.fixture(autouse=True)
async def _isolated_queues(session_factory):
    await purge_open_preparations(session_factory)
    yield
    await purge_open_preparations(session_factory)


class _SimulatedCrash(BaseException):
    """Passa por `except Exception`: o processo "morre" sem finalizar nada."""


def _ids(count: int) -> list[str]:
    return [f"BR1_{uuid.uuid4().int % 10**10}" for _ in range(count)]


class Sample:
    """Uma amostra sintética coletada, com as configs de preparação REAL do teste."""

    def __init__(self, tmp_path, count: int, *, seeds: int = 1) -> None:
        self.patch = unique_patch()
        self.ids = _ids(count)
        self.h: Harness = one_seed(tmp_path, []) if seeds == 1 else two_seeds(tmp_path)
        self.h.filters = MatchFilterConfig(420, 11, frozenset({self.patch}))
        for match_id in self.ids:
            self.h.stub.add_match(
                match_id,
                seed_puuid=SEED_A_PUUID,
                patch=self.patch,
                purchases=PURCHASES,
                other_purchases=(
                    (130_000, 3006),
                ),  # o outro jogador (fora do recorte) também compra
            )
        (tmp_path / "cfg").mkdir(exist_ok=True)
        base = write_prepare_configs(
            tmp_path / "cfg", scope_patch=self.patch, mapped_patches=(self.patch,)
        )
        self.settings: Settings = base.model_copy(update={"subject_hash_secret": HASH_SECRET})
        self.scope_path = tmp_path / "cfg" / "scope.yaml"
        self.catalog = stub_catalog_factory(StubCatalogSource())

    @property
    def raw_store(self):
        return self.h.raw_store

    async def cleanup(self, session_factory) -> None:
        for match_id in self.ids:
            await reset_match(session_factory, match_id)

    async def import_all(self, session_factory):
        fetched = self.h.manifest().fetched_ids()
        enqueue = await enqueue_collected_imports(session_factory, self.raw_store, fetched)
        drain = await drain_local(
            session_factory, self.raw_store, self.settings, catalog_source_factory=self.catalog
        )
        return enqueue, drain


@pytest.fixture(autouse=True)
def _no_http_client_for_imports(monkeypatch):
    """A importação da coleta NUNCA busca: construir o cliente HTTP do worker é proibido."""

    def forbidden():
        raise AssertionError("a importação a partir do raw local não pode criar cliente HTTP")

    monkeypatch.setattr(ingestion_application, "_default_riot_client", forbidden)


async def _count(session_factory, model, *conditions) -> int:
    async with unit_of_work(session_factory) as session:
        return int(
            await session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
        )


async def _jobs(session_factory, match_id: str) -> list[ImportJobModel]:
    async with unit_of_work(session_factory) as session:
        rows = await session.scalars(
            select(ImportJobModel).where(ImportJobModel.requested_match_id == match_id)
        )
        return list(rows)


async def _expire_lease(session_factory, ids: list[str], kind: str) -> None:
    async with unit_of_work(session_factory) as session:
        await session.execute(
            update(ImportJobModel)
            .where(
                ImportJobModel.requested_match_id.in_(ids),
                ImportJobModel.kind == kind,
                ImportJobModel.state == "RUNNING",
            )
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=5))
        )


# ---------------------------------------------------------------------------
# ponta a ponta: coleta stub -> raw -> import local -> preparação READY -> relatórios
# ---------------------------------------------------------------------------


async def test_collect_import_prepare_and_report_without_personal_data(
    session_factory, tmp_path
) -> None:
    sample = Sample(tmp_path, 3)
    try:
        run = await sample.h.run()
        assert run.stop_reason == StopReason.COMPLETED and run.new_valid == 3
        calls_after_collection = len(sample.h.stub.calls)

        enqueue, drain = await sample.import_all(session_factory)

        assert (enqueue.enqueued, enqueue.already_requested, enqueue.raw_incomplete) == (3, 0, 0)
        assert (drain.import_cycles, drain.preparation_cycles) == (3, 3)
        assert len(sample.h.stub.calls) == calls_after_collection  # zero requisições novas
        assert await _count(session_factory, MatchModel, MatchModel.match_id.in_(sample.ids)) == 3
        assert (
            await _count(
                session_factory,
                ParticipantModel,
                ParticipantModel.match_id.in_(sample.ids),
            )
            == 6
        )

        reconstruction = (
            await build_reconstruction_report(
                session_factory, sample.scope_path, match_ids=sample.ids
            )
        ).as_dict()
        diversity = await build_diversity_report(
            session_factory, sample.scope_path, match_ids=sample.ids
        )
        integrity = await check_integrity(
            session_factory, sample.scope_path, raw_store=sample.raw_store, match_ids=sample.ids
        )
        collection = build_collection_report(sample.h.manifest())

        assert reconstruction["matchesImported"] == 3
        assert reconstruction["preparation"] == {"READY": 3}
        assert reconstruction["matchesReconstructed"] == reconstruction["matchesExtracted"] == 3
        assert reconstruction["byPatch"][sample.patch]["matches"] == 3
        points = reconstruction["points"]
        # a extração cobre os DOIS participantes; só o da semente está no recorte (UTILITY/89)
        assert points["total"] == 6 and points["eligible"] + points["ineligible"] == 6
        recorte = reconstruction["inRecorte"]
        assert recorte["points"] == 3 and recorte["eligible"] <= 3
        assert reconstruction["reasons"]["OUT_OF_SCOPE"] >= 3  # o outro jogador, nas 3 partidas
        assert "OUT_OF_SCOPE" not in recorte["reasons"]  # o recorte só traz o que está DENTRO
        assert "DENTRO DO RECORTE: 3 pontos" in render_reconstruction_report(reconstruction)
        assert reconstruction["states"]["total"] >= 3
        assert diversity["matches"] == 3 and diversity["participations"] == 6
        assert diversity["distinctPlayers"] == 4  # a semente + 3 outros jogadores
        assert diversity["concentration"]["top1PlayerMatchShare"] == 100.0  # a semente em todas
        assert diversity["matchesPerPlayer"]["max"] == 3
        assert diversity["scope"]["participantsByPatchAndChampion"][sample.patch] == {"89": 3}
        assert diversity["scope"]["matchesWithAnyInScopeParticipant"] == 3
        assert integrity["ok"] and integrity["readyChecked"] == 3
        assert collection["totals"]["fetched"] == 3

        hashes = [
            compute_real_subject_hash(puuid, HASH_SECRET)
            for puuid in (SEED_A_PUUID, *(f"puuid-outro-{m}" for m in sample.ids))
        ]
        outputs = [
            json.dumps(reconstruction),
            render_reconstruction_report(reconstruction),
            json.dumps(diversity),
            render_diversity_report(diversity),
            json.dumps(integrity),
            render_integrity_report(integrity),
            json.dumps(collection),
        ]
        for text in outputs:
            for forbidden in (*hashes, SEED_A_PUUID, "puuid-outro", *sample.ids):
                assert forbidden not in text
    finally:
        await sample.cleanup(session_factory)


async def test_repeated_import_is_idempotent_and_never_duplicates(
    session_factory, tmp_path
) -> None:
    sample = Sample(tmp_path, 2)
    try:
        await sample.h.run()
        await sample.import_all(session_factory)
        again, drain = await sample.import_all(session_factory)  # repete tudo

        assert (again.enqueued, again.already_requested) == (0, 2)
        assert (drain.import_cycles, drain.preparation_cycles) == (0, 0)
        for match_id in sample.ids:
            jobs = await _jobs(session_factory, match_id)
            assert sorted((j.kind, j.state) for j in jobs) == [
                ("IMPORT", "SUCCEEDED"),
                ("PREPARE", "SUCCEEDED"),
            ]
            assert await _count(session_factory, MatchModel, MatchModel.match_id == match_id) == 1
            assert (
                await _count(
                    session_factory,
                    DecisionExtractionModel,
                    DecisionExtractionModel.match_id == match_id,
                )
                == 1
            )
        rerun = await sample.h.run()  # e recoletar não traz nada novo nem duplica
        assert rerun.new_valid == 0 and rerun.duplicates == 2
    finally:
        await sample.cleanup(session_factory)


# ---------------------------------------------------------------------------
# crash: entre o raw e a persistência, e durante a preparação
# ---------------------------------------------------------------------------


async def test_crash_between_raw_and_persist_resumes_without_duplicating_or_network(
    session_factory, tmp_path, monkeypatch
) -> None:
    sample = Sample(tmp_path, 1)
    [match_id] = sample.ids
    try:
        await sample.h.run()
        calls = len(sample.h.stub.calls)
        original = ingestion_application._persist_result

        async def die(*_args, **_kwargs):
            raise _SimulatedCrash

        monkeypatch.setattr(ingestion_application, "_persist_result", die)
        with pytest.raises(_SimulatedCrash):
            await sample.import_all(session_factory)

        [job] = [j for j in await _jobs(session_factory, match_id) if j.kind == "IMPORT"]
        assert job.state == "RUNNING"  # o processo morreu com a posse
        assert await _count(session_factory, MatchModel, MatchModel.match_id == match_id) == 0
        assert not (
            await check_integrity(session_factory, sample.scope_path, match_ids=[match_id])
        )["violationsTotal"]  # nada parcial no banco

        monkeypatch.setattr(ingestion_application, "_persist_result", original)
        await _expire_lease(session_factory, [match_id], "IMPORT")
        _, drain = await sample.import_all(session_factory)

        assert drain.import_cycles == 1 and drain.preparation_cycles == 1
        [job] = [j for j in await _jobs(session_factory, match_id) if j.kind == "IMPORT"]
        assert (job.state, job.attempts) == ("SUCCEEDED", 2)
        assert await _count(session_factory, MatchModel, MatchModel.match_id == match_id) == 1
        assert len(sample.h.stub.calls) == calls  # a retomada não buscou nada
    finally:
        await sample.cleanup(session_factory)


async def test_crash_during_preparation_resumes_without_duplicating(
    session_factory, tmp_path, monkeypatch
) -> None:
    sample = Sample(tmp_path, 1)
    [match_id] = sample.ids
    try:
        await sample.h.run()

        async def die(*_args, **_kwargs):
            raise _SimulatedCrash

        original = preparation.extract_decisions
        monkeypatch.setattr(preparation, "extract_decisions", die)
        with pytest.raises(_SimulatedCrash):
            await sample.import_all(session_factory)

        assert (
            await _count(
                session_factory,
                MatchReconstructionModel,
                MatchReconstructionModel.match_id == match_id,
            )
            == 1
        )  # reconstruiu...
        assert (
            await _count(
                session_factory,
                DecisionExtractionModel,
                DecisionExtractionModel.match_id == match_id,
            )
            == 0
        )  # ...mas não extraiu: nada de decisões, nada de READY
        report = await build_reconstruction_report(
            session_factory, sample.scope_path, match_ids=[match_id]
        )
        assert report.preparation == {"RUNNING": 1} and report.points_total == 0
        integrity = await check_integrity(session_factory, sample.scope_path, match_ids=[match_id])
        assert integrity["ok"]  # RUNNING não é READY: sem violação, só ainda não analisável

        monkeypatch.setattr(preparation, "extract_decisions", original)
        await _expire_lease(session_factory, [match_id], "PREPARE")
        await sample.import_all(session_factory)

        report = await build_reconstruction_report(
            session_factory, sample.scope_path, match_ids=[match_id]
        )
        assert report.preparation == {"READY": 1} and report.matches_extracted == 1
        assert (
            await _count(
                session_factory,
                DecisionExtractionModel,
                DecisionExtractionModel.match_id == match_id,
            )
            == 1
        )
        assert (
            await _count(
                session_factory,
                MatchReconstructionModel,
                MatchReconstructionModel.match_id == match_id,
            )
            == 1
        )  # a reconstrução não foi duplicada
        assert (
            await _count(
                session_factory, DecisionPointModel, DecisionPointModel.match_id == match_id
            )
            == report.points_total
        )
    finally:
        await sample.cleanup(session_factory)


# ---------------------------------------------------------------------------
# nenhuma partida parcial analisável
# ---------------------------------------------------------------------------


async def test_partial_or_invalid_raw_pairs_never_become_analysable(
    session_factory, tmp_path
) -> None:
    sample = Sample(tmp_path, 0)
    [only_match, bad_pair] = _ids(2)
    sample.ids = [only_match, bad_pair]
    try:
        match_doc, timeline_doc = real_pair(only_match, seed_puuid=SEED_A_PUUID, patch=sample.patch)
        body = json.dumps(match_doc).encode()
        import hashlib

        sample.raw_store.store(only_match, "match", body, hashlib.sha256(body).hexdigest())

        mismatch_match, mismatch_timeline = real_pair(
            bad_pair, seed_puuid=SEED_A_PUUID, patch=sample.patch
        )
        mismatch_timeline["info"]["participants"][0]["puuid"] = "puuid-diferente"
        for kind, doc in (("match", mismatch_match), ("timeline", mismatch_timeline)):
            raw = json.dumps(doc).encode()
            sample.raw_store.store(bad_pair, kind, raw, hashlib.sha256(raw).hexdigest())

        enqueue = await enqueue_collected_imports(
            session_factory, sample.raw_store, [only_match, bad_pair]
        )
        assert (enqueue.enqueued, enqueue.raw_incomplete) == (1, 1)  # o incompleto nem vira job

        # mesmo forçando um job para o par incompleto, o import local falha sem criar partida
        await request_import(session_factory, only_match, ImportMode.RIOT)
        await drain_local(
            session_factory,
            sample.raw_store,
            sample.settings,
            catalog_source_factory=sample.catalog,
        )

        jobs = {
            m: [j for j in await _jobs(session_factory, m) if j.kind == "IMPORT"]
            for m in sample.ids
        }
        assert [j.error_code for j in jobs[only_match]] == ["RAW_MATCH_NOT_CACHED"]
        assert [j.error_code for j in jobs[bad_pair]] == ["MATCH_TIMELINE_IDENTITY_MISMATCH"]
        assert all(j.state == "FAILED" for js in jobs.values() for j in js)
        assert await _count(session_factory, MatchModel, MatchModel.match_id.in_(sample.ids)) == 0
        assert (
            await _count(
                session_factory, TimelineEventModel, TimelineEventModel.match_id.in_(sample.ids)
            )
            == 0
        )

        app = create_app(sample.settings)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                for match_id in sample.ids:
                    assert (await client.get(f"/api/v1/matches/{match_id}")).status_code == 404
                listed = await client.get("/api/v1/matches", params={"limit": 100})
                assert all(m not in listed.text for m in sample.ids)
        report = await build_reconstruction_report(
            session_factory, sample.scope_path, match_ids=sample.ids
        )
        assert report.matches_imported == 0 and report.points_total == 0
    finally:
        await sample.cleanup(session_factory)


async def test_integrity_check_detects_real_violations(session_factory, tmp_path) -> None:
    sample = Sample(tmp_path, 0)
    [bare, ready_fake] = _ids(2)
    sample.ids = [bare, ready_fake]
    try:
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyIngestionRepository(session)
            from riftlab.ingestion.domain import Match

            for match_id in (bare, ready_fake):
                await repo.add_match(
                    Match(
                        match_id=match_id,
                        queue=420,
                        map_id=11,
                        patch=sample.patch,
                        start_at=datetime.now(UTC),
                        duration_seconds=1800,
                        raw_hash="0" * 64,
                    )
                )
        # `ready_fake`: importação RIOT concluída + preparação "pronta" SEM extração
        await request_import(session_factory, ready_fake, ImportMode.RIOT)
        async with unit_of_work(session_factory) as session:
            await SqlAlchemyIngestionRepository(session).enqueue_preparation(
                ready_fake, ImportMode.RIOT
            )
            await session.execute(
                update(ImportJobModel)
                .where(ImportJobModel.requested_match_id == ready_fake)
                .values(state="SUCCEEDED", match_id=ready_fake)
            )

        data = await check_integrity(
            session_factory, sample.scope_path, raw_store=sample.raw_store, match_ids=sample.ids
        )

        assert not data["ok"]
        assert data["violations"] == {
            "MATCH_WITHOUT_PARTICIPANTS": 2,
            "MATCH_WITHOUT_EVENTS": 2,
            "ORPHAN_MATCH": 1,  # `bare` não tem importação concluída
            "READY_WITHOUT_EXTRACTION": 1,
            "RAW_PAIR_MISSING": 1,  # `ready_fake` é real e não tem o par bruto
        }
        assert "VIOLAÇÕES" in render_integrity_report(data)
    finally:
        await sample.cleanup(session_factory)


# ---------------------------------------------------------------------------
# 401 no meio + retomada, tetos e filtros com contagem
# ---------------------------------------------------------------------------


async def test_401_in_the_middle_then_resume_ends_with_every_match_ready_exactly_once(
    session_factory, tmp_path
) -> None:
    sample = Sample(tmp_path, 4)
    try:
        sample.h.stub.fail_auth_after(7)  # conta, lista, 2 partidas completas... 401 na 3ª
        first = await sample.h.run()
        assert first.stop_reason == StopReason.AUTH_FAILED and first.new_valid == 2
        await sample.import_all(session_factory)  # o que já foi coletado entra no banco
        assert await _count(session_factory, MatchModel, MatchModel.match_id.in_(sample.ids)) == 2

        blocked = await sample.h.run()
        assert blocked.stop_reason == StopReason.AUTH_BLOCKED
        sample.h.stub.fix_auth()  # troca manual da chave
        resumed = await sample.h.run(resume=True)
        assert resumed.stop_reason == StopReason.COMPLETED and resumed.new_valid == 2
        await sample.import_all(session_factory)

        assert await _count(session_factory, MatchModel, MatchModel.match_id.in_(sample.ids)) == 4
        assert (
            await _count(
                session_factory,
                ImportJobModel,
                ImportJobModel.requested_match_id.in_(sample.ids),
                ImportJobModel.kind == "IMPORT",
            )
            == 4
        )  # um job de importação por partida, nenhum duplicado
        report = await build_reconstruction_report(
            session_factory, sample.scope_path, match_ids=sample.ids
        )
        assert report.preparation == {"READY": 4}
        assert (await check_integrity(session_factory, sample.scope_path, match_ids=sample.ids))[
            "ok"
        ]
        totals = build_collection_report(sample.h.manifest())["totals"]
        assert totals["fetched"] == 4 and totals["runs"] == 3
    finally:
        await sample.cleanup(session_factory)


async def test_ceiling_and_filters_only_valid_matches_reach_the_database(
    session_factory, tmp_path
) -> None:
    sample = Sample(tmp_path, 3)
    wrong_queue, wrong_patch = _ids(2)
    sample.h.stub.add_match(wrong_queue, seed_puuid=SEED_A_PUUID, patch=sample.patch, queue=440)
    sample.h.stub.add_match(wrong_patch, seed_puuid=SEED_A_PUUID, patch="1.1")
    sample.ids += [wrong_queue, wrong_patch]
    try:
        first = await sample.h.run(ceilings=CollectionCeilings(500, 2, 300))
        assert first.stop_reason == StopReason.MAX_MATCHES and first.new_valid == 2
        second = await sample.h.run()
        assert second.stop_reason == StopReason.COMPLETED

        manifest = sample.h.manifest()
        assert len(manifest.fetched_ids()) == 3
        assert manifest.excluded_by_reason() == {
            "QUEUE_OUT_OF_SCOPE": 1,
            "PATCH_OUT_OF_SCOPE": 1,
        }  # perdas contadas por filtro
        await sample.import_all(session_factory)

        in_db = set(await _fetch_ids(session_factory, sample.ids))
        assert in_db == set(manifest.fetched_ids())  # só as válidas chegaram ao banco
        assert wrong_queue not in in_db and wrong_patch not in in_db
        report = await build_reconstruction_report(
            session_factory, sample.scope_path, match_ids=sample.ids
        )
        assert report.matches_imported == 3 and report.preparation == {"READY": 3}
    finally:
        await sample.cleanup(session_factory)


async def _fetch_ids(session_factory, ids: list[str]) -> list[str]:
    async with unit_of_work(session_factory) as session:
        return list(
            await session.scalars(select(MatchModel.match_id).where(MatchModel.match_id.in_(ids)))
        )


# ---------------------------------------------------------------------------
# importador: "local primeiro"
# ---------------------------------------------------------------------------


async def test_worker_imports_a_collected_match_without_key_client_or_cooldown(
    session_factory, tmp_path
) -> None:
    """Com o raw completo, o job RIOT do worker é importado pela fonte local: sem chave,
    sem cliente HTTP (a fixture proíbe) e sem esperar o cooldown de rede."""
    sample = Sample(tmp_path, 1)
    [match_id] = sample.ids
    try:
        await sample.h.run()
        await request_import(session_factory, match_id, ImportMode.RIOT)
        async with unit_of_work(session_factory) as session:
            await SqlAlchemyIngestionRepository(session).extend_riot_cooldown_until(
                datetime.now(UTC) + timedelta(hours=1)
            )
        settings = sample.settings.model_copy(update={"riot_api_key": None})

        processed = await ingestion_application.process_next_job(
            session_factory, sample.raw_store, settings
        )

        assert processed is True
        [job] = [j for j in await _jobs(session_factory, match_id) if j.kind == "IMPORT"]
        assert job.state == "SUCCEEDED"  # não foi adiada pelo cooldown compartilhado
        assert await _count(session_factory, MatchModel, MatchModel.match_id == match_id) == 1
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(RiotRateLimitModel.__table__.delete())
        await sample.cleanup(session_factory)
