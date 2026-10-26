"""Placar legado e leitura HTTP no PostgreSQL isolado da suíte de integração."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from riftlab.ingestion.application import _persist_result
from riftlab.ingestion.domain import (
    ImportJob,
    ImportJobState,
    ImportMode,
    Match,
    MatchParticipantSourceDTO,
    MatchSourceResult,
    Participant,
)
from riftlab.ingestion.final_items_backfill import backfill_final_items
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import ImportJobModel, ParticipantFinalItemsModel
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.ingestion.score import ParticipantScore
from riftlab.ingestion.score_backfill import ScoreBackfillConflict, backfill_match_score
from riftlab.shared.db import unit_of_work
from riftlab.shared.domain import SourceOrigin


def _id() -> str:
    return f"BR1_{uuid.uuid4().int % 10**15}"


def _raw(match_id: str, *, first_kills: int = 5) -> bytes:
    return json.dumps(
        {
            "metadata": {"matchId": match_id},
            "info": {
                "endOfGameResult": "GameComplete",
                "participants": [
                    {
                        "participantId": 1,
                        "championId": 51,
                        "teamId": 100,
                        "win": True,
                        "kills": first_kills,
                        "deaths": 0,
                        "assists": 8,
                        "totalMinionsKilled": 180,
                        "neutralMinionsKilled": 10,
                        "item0": 1001,
                        "item1": 0,
                        "item2": 0,
                        "item3": 0,
                        "item4": 0,
                        "item5": 0,
                        "item6": 3340,
                    },
                    {
                        "participantId": 2,
                        "championId": 81,
                        "teamId": 200,
                        "win": False,
                        "item0": 0,
                        "item1": 0,
                        "item2": 0,
                        "item3": 0,
                        "item4": 0,
                        "item5": 0,
                        "item6": 0,
                    },
                ],
            },
        }
    ).encode()


async def _seed(
    session_factory, match_id: str, digest: str, *, score: ParticipantScore | None = None
):
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        await repo.add_match(Match(match_id, 420, 11, "16.17", datetime.now(UTC), 1800, digest))
        await session.flush()
        await repo.add_participant(
            Participant(match_id, 1, 51, 100, "synthetic-hash-1", score=score or ParticipantScore())
        )
        await repo.add_participant(Participant(match_id, 2, 81, 200, "synthetic-hash-2"))


async def test_backfill_updates_only_missing_fields_and_http_has_no_identity(
    session_factory, app_client, tmp_path
) -> None:
    match_id = _id()
    body = _raw(match_id)
    digest = hashlib.sha256(body).hexdigest()
    store = DurableRawMatchStore(tmp_path / "raw")
    store.store(match_id, "match", body, digest)
    await _seed(session_factory, match_id, digest)

    before_files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (tmp_path / "raw").rglob("*")
        if p.is_file()
    }
    first = await backfill_match_score(session_factory, tmp_path / "raw", match_id)
    second = await backfill_match_score(session_factory, tmp_path / "raw", match_id)
    after_files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (tmp_path / "raw").rglob("*")
        if p.is_file()
    }
    assert before_files == after_files
    assert first.status == "UPDATED" and first.fields_filled == 7
    assert second.status == "UNCHANGED" and second.fields_filled == 0

    response = await app_client.get(f"/api/v1/matches/{match_id}")
    assert response.status_code == 200
    first_participant, second_participant = response.json()["participants"]
    assert first_participant["win"] is True
    assert first_participant["deaths"] == 0
    assert first_participant["totalMinionsKilled"] == 180
    assert first_participant["neutralMinionsKilled"] == 10
    assert second_participant["win"] is False
    assert second_participant["kills"] is None
    assert not any(
        token in response.text
        for token in ("synthetic-hash", "subjectHash", "puuid", "riotId", "isOwnAccount")
    )


async def test_backfill_missing_corrupt_and_identity_mismatch_do_not_write(
    session_factory, tmp_path
) -> None:
    match_id = _id()
    body = _raw(match_id)
    digest = hashlib.sha256(body).hexdigest()
    await _seed(session_factory, match_id, digest)
    raw_dir = tmp_path / "raw"
    assert (await backfill_match_score(session_factory, raw_dir, match_id)).status == "RAW_ABSENT"
    assert not raw_dir.exists()
    store = DurableRawMatchStore(raw_dir)
    store.store(match_id, "match", body, digest)
    (raw_dir / f"{match_id}.match.ref").write_text("0" * 64)
    assert (await backfill_match_score(session_factory, raw_dir, match_id)).status == "RAW_INVALID"
    (raw_dir / f"{match_id}.match.ref").write_text(digest)
    wrong_body = _raw(_id())
    wrong_digest = hashlib.sha256(wrong_body).hexdigest()
    other_id = _id()
    other_store = DurableRawMatchStore(tmp_path / "wrong")
    other_store.store(other_id, "match", wrong_body, wrong_digest)
    await _seed(session_factory, other_id, wrong_digest)
    with pytest.raises(ScoreBackfillConflict, match="RAW_SCHEMA_OR_IDENTITY_MISMATCH"):
        await backfill_match_score(session_factory, tmp_path / "wrong", other_id)
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        assert (await repo.get_participant(match_id, 1)).score == ParticipantScore()
        assert (await repo.get_participant(other_id, 1)).score == ParticipantScore()


async def test_backfill_hash_and_existing_score_divergence_are_atomic(
    session_factory, tmp_path
) -> None:
    match_id = _id()
    body = _raw(match_id)
    digest = hashlib.sha256(body).hexdigest()
    store = DurableRawMatchStore(tmp_path / "raw")
    store.store(match_id, "match", body, digest)
    await _seed(session_factory, match_id, "f" * 64)
    with pytest.raises(ScoreBackfillConflict, match="MATCH_HASH_MISMATCH"):
        await backfill_match_score(session_factory, tmp_path / "raw", match_id)

    divergent_id = _id()
    divergent_body = _raw(divergent_id)
    divergent_digest = hashlib.sha256(divergent_body).hexdigest()
    store.store(divergent_id, "match", divergent_body, divergent_digest)
    await _seed(session_factory, divergent_id, divergent_digest, score=ParticipantScore(kills=99))
    with pytest.raises(ScoreBackfillConflict, match="SCORE_DIVERGENCE"):
        await backfill_match_score(session_factory, tmp_path / "raw", divergent_id)
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        assert (await repo.get_participant(match_id, 1)).score == ParticipantScore()
        assert (await repo.get_participant(divergent_id, 1)).score == ParticipantScore(kills=99)


async def test_final_items_backfill_is_hash_checked_idempotent_and_concurrent(
    session_factory, tmp_path
) -> None:
    match_id = _id()
    body = _raw(match_id)
    digest = hashlib.sha256(body).hexdigest()
    raw_dir = tmp_path / "final-items"
    store = DurableRawMatchStore(raw_dir)
    store.store(match_id, "match", body, digest)
    await _seed(session_factory, match_id, digest)
    first, second = await asyncio.gather(
        backfill_final_items(session_factory, raw_dir, match_id),
        backfill_final_items(session_factory, raw_dir, match_id),
    )
    assert sorted(result.status for result in (first, second)) == ["UNCHANGED", "UPDATED"]
    async with session_factory() as session:
        rows = (
            await session.scalars(
                select(ParticipantFinalItemsModel).where(
                    ParticipantFinalItemsModel.match_id == match_id
                )
            )
        ).all()
        assert {(row.participant_id, tuple(row.items), row.trinket_item_id) for row in rows} == {
            (1, (1001,), 3340),
            (2, (), None),
        }
    wrong_id = _id()
    wrong_body = _raw(wrong_id)
    wrong_digest = hashlib.sha256(wrong_body).hexdigest()
    wrong_store = DurableRawMatchStore(tmp_path / "wrong-final-items")
    wrong_store.store(wrong_id, "match", wrong_body, wrong_digest)
    await _seed(session_factory, wrong_id, "f" * 64)
    with pytest.raises(ScoreBackfillConflict, match="MATCH_HASH_MISMATCH"):
        await backfill_final_items(session_factory, tmp_path / "wrong-final-items", wrong_id)
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(ParticipantFinalItemsModel).where(
                    ParticipantFinalItemsModel.match_id == wrong_id
                )
            )
            is None
        )


async def test_legacy_raw_backfill_reads_without_migrating_or_modifying_files(
    session_factory, tmp_path
) -> None:
    match_id = _id()
    body = _raw(match_id)
    digest = hashlib.sha256(body).hexdigest()
    raw_dir = tmp_path / "legacy"
    raw_dir.mkdir()
    (raw_dir / f"{match_id}.match.json").write_bytes(body)
    (raw_dir / f"{match_id}.match.sha256").write_text(digest)
    before = sorted(p.name for p in raw_dir.rglob("*"))
    await _seed(session_factory, match_id, digest)
    result = await backfill_match_score(session_factory, raw_dir, match_id)
    assert result.status == "UPDATED"
    assert before == sorted(p.name for p in raw_dir.rglob("*"))
    assert (raw_dir / f"{match_id}.match.json").read_bytes() == body


async def test_reimport_does_not_rewrite_original_score(session_factory) -> None:
    match_id = _id()
    await _seed(session_factory, match_id, "a" * 64, score=ParticipantScore(kills=4, deaths=0))
    job = ImportJob(
        id=uuid.uuid4(),
        requested_match_id=match_id,
        mode=ImportMode.RIOT,
        state=ImportJobState.RUNNING,
        lease_generation=1,
    )
    async with unit_of_work(session_factory) as session:
        await SqlAlchemyIngestionRepository(session).create_import_job(job)
    result = MatchSourceResult(
        match_id=match_id,
        origin=SourceOrigin.COMPLETED_MATCH,
        schema_version="riot-match-v5",
        raw_hash="b" * 64,
        queue=420,
        map_id=11,
        patch="16.17",
        start_at=datetime.now(UTC),
        duration_seconds=1800,
        participants=(
            MatchParticipantSourceDTO(
                1, 51, 100, "synthetic-hash-1", score=ParticipantScore(kills=99, deaths=12)
            ),
        ),
        frames=(),
        events=(),
    )
    await _persist_result(session_factory, job, result, None)
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        assert (await repo.get_participant(match_id, 1)).score == ParticipantScore(
            kills=4, deaths=0
        )
        await session.execute(
            delete(ImportJobModel).where(ImportJobModel.requested_match_id == match_id)
        )
