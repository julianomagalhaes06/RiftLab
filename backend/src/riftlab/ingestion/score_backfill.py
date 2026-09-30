"""Backfill explícito, offline e transacional de placar por partida antiga."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore, RawStoreCorruptedError
from riftlab.ingestion.models import MatchModel, ParticipantModel
from riftlab.ingestion.score import ParticipantScore, scores_from_riot_match
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import SourceSchemaError


class ScoreBackfillConflict(ValueError):
    """Identidade/hash/valor persistido diverge: nenhuma escrita é confirmada."""


@dataclass(frozen=True, slots=True)
class ScoreBackfillResult:
    status: str
    participants: int = 0
    fields_filled: int = 0


async def backfill_match_score(
    session_factory: async_sessionmaker[AsyncSession], raw_dir: Path, match_id: str
) -> ScoreBackfillResult:
    """Uma partida por transação; nem cria cache local nem possui cliente HTTP."""
    raw_store = DurableRawMatchStore(raw_dir, create=False)
    try:
        body = raw_store.read_if_valid_read_only(match_id, "match")
    except (RawStoreCorruptedError, ValueError, OSError):
        return ScoreBackfillResult(status="RAW_INVALID")
    if body is None:
        return ScoreBackfillResult(status="RAW_ABSENT")
    actual_hash = hashlib.sha256(body).hexdigest()
    try:
        raw_scores = scores_from_riot_match(match_id, body)
    except SourceSchemaError as exc:
        raise ScoreBackfillConflict("RAW_SCHEMA_OR_IDENTITY_MISMATCH") from exc

    async with unit_of_work(session_factory) as session:
        match = await session.scalar(
            select(MatchModel).where(MatchModel.match_id == match_id).with_for_update()
        )
        if match is None or match.raw_hash != actual_hash:
            raise ScoreBackfillConflict("MATCH_HASH_MISMATCH")
        participants = (
            await session.scalars(
                select(ParticipantModel)
                .where(ParticipantModel.match_id == match_id)
                .order_by(ParticipantModel.participant_id)
                .with_for_update()
            )
        ).all()
        if {p.participant_id for p in participants} != set(raw_scores):
            raise ScoreBackfillConflict("PARTICIPANT_SET_MISMATCH")
        for row in participants:
            champion_id, team, score = raw_scores[row.participant_id]
            if row.champion_id != champion_id or row.team != team:
                raise ScoreBackfillConflict("PARTICIPANT_IDENTITY_MISMATCH")
            for field in ParticipantScore.__dataclass_fields__:
                existing = getattr(row, field)
                observed = getattr(score, field)
                if existing is not None and observed is not None and existing != observed:
                    raise ScoreBackfillConflict("SCORE_DIVERGENCE")
        # Todas as identidades, hashes e divergências foram conferidos antes da
        # primeira atribuição. Uma exceção posterior ainda reverte a transação.
        filled = 0
        for row in participants:
            score = raw_scores[row.participant_id][2]
            for field in ParticipantScore.__dataclass_fields__:
                observed = getattr(score, field)
                if getattr(row, field) is None and observed is not None:
                    setattr(row, field, observed)
                    filled += 1
    return ScoreBackfillResult(
        status="UPDATED" if filled else "UNCHANGED",
        participants=len(participants),
        fields_filled=filled,
    )
