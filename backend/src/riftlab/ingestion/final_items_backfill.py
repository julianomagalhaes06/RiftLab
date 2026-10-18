"""Preenchimento offline, com identidade e hash conferidos antes da escrita."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.ingestion.final_items import parse_final_items
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore, RawStoreCorruptedError
from riftlab.ingestion.models import MatchModel, ParticipantFinalItemsModel, ParticipantModel
from riftlab.ingestion.score import scores_from_riot_match
from riftlab.ingestion.score_backfill import ScoreBackfillConflict, ScoreBackfillResult


async def backfill_final_items(
    sf: async_sessionmaker[AsyncSession], raw_dir: Path, match_id: str
) -> ScoreBackfillResult:
    store = DurableRawMatchStore(raw_dir, create=False)
    try:
        body = store.read_if_valid_read_only(match_id, "match")
    except (RawStoreCorruptedError, ValueError, OSError):
        return ScoreBackfillResult("RAW_INVALID")
    if body is None:
        return ScoreBackfillResult("RAW_ABSENT")
    identities = scores_from_riot_match(match_id, body)
    doc = json.loads(body)
    finals = {p["participantId"]: parse_final_items(p) for p in doc["info"]["participants"]}
    digest = hashlib.sha256(body).hexdigest()
    async with sf.begin() as session:
        match = await session.scalar(
            select(MatchModel).where(MatchModel.match_id == match_id).with_for_update()
        )
        if match is None or match.raw_hash != digest:
            raise ScoreBackfillConflict("MATCH_HASH_MISMATCH")
        participants = (
            await session.scalars(
                select(ParticipantModel).where(ParticipantModel.match_id == match_id)
            )
        ).all()
        if {p.participant_id for p in participants} != set(identities):
            raise ScoreBackfillConflict("PARTICIPANT_SET_MISMATCH")
        filled = 0
        for p in participants:
            if (p.champion_id, p.team) != identities[p.participant_id][:2]:
                raise ScoreBackfillConflict("PARTICIPANT_IDENTITY_MISMATCH")
            value = finals[p.participant_id]
            if value is None:
                continue
            old = await session.get(ParticipantFinalItemsModel, (match_id, p.participant_id))
            if old is not None:
                if (old.items, old.trinket_item_id, old.role_bound_item_id, old.raw_hash) != (
                    list(value.items),
                    value.trinket_item_id,
                    value.role_bound_item_id,
                    digest,
                ):
                    raise ScoreBackfillConflict("FINAL_ITEMS_DIVERGENCE")
                continue
            session.add(
                ParticipantFinalItemsModel(
                    match_id=match_id,
                    participant_id=p.participant_id,
                    items=list(value.items),
                    trinket_item_id=value.trinket_item_id,
                    role_bound_item_id=value.role_bound_item_id,
                    raw_hash=digest,
                )
            )
            filled += 1
    return ScoreBackfillResult("UPDATED" if filled else "UNCHANGED", len(participants), filled)
