"""CC-304: study attempts — choice before reveal (docs/learning-design.md).

Options are the engine's top three legal candidates, shown in a neutral order that
is stable per attempt (shuffled with the attempt id as seed); before REVEALED the
public state carries no rank, score, cost or historical purchase. Hints only show
available facts. Reveal creates the analysis idempotently (one per attempt) and is
monotonic. Answers lock after the first one; a new choice needs a new attempt.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.analyses.application import _compute, create_analysis, request_hash
from riftlab.champions.models import ChampionModel
from riftlab.decisions.models import DecisionPointModel
from riftlab.learning.models import LearningAttemptModel
from riftlab.shared.errors import ConflictError, InvalidRequestError, NotFoundError

EXERCISE_VERSION = "study_v1"


def _public(row: LearningAttemptModel, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    revealed = row.status == "REVEALED"
    hints: list[dict[str, Any]] = []
    if row.hint_level >= 1:
        hints.extend((extra or {}).get("threatHints", []))
    if row.hint_level >= 2:
        for option in row.options:
            tags = ", ".join(option.get("tags") or []) or "sem atributos marcados no catálogo"
            hints.append(
                {
                    "level": 2,
                    "text": f"{option.get('name') or option['itemId']}: faltam "
                    f"{option.get('remainingCost')} de ouro; atributos: {tags}.",
                    "source": "candidates.remainingCost, catalog.items.tags",
                }
            )
    return {
        "attemptId": str(row.id),
        "decisionId": str(row.decision_id),
        "status": row.status,
        "objective": row.objective,
        "context": (extra or {}).get("context", {}),
        "options": [{"itemId": o["itemId"], "name": o.get("name")} for o in row.options],
        "hintLevel": row.hint_level,
        "hints": hints,
        "reasonCodes": row.reason_codes,
        "selectedItemId": row.selected_item_id,
        "skipped": row.skipped,
        "rationale": row.rationale,
        "analysisId": str(row.analysis_id) if revealed and row.analysis_id else None,
        "comparison": (extra or {}).get("comparison") if revealed else None,
        "historicalItemId": (extra or {}).get("historicalItemId") if revealed else None,
    }


async def _extra(session: AsyncSession, row: LearningAttemptModel) -> dict[str, Any]:
    point = await session.get(DecisionPointModel, row.decision_id)
    assert point is not None
    values = (point.features or {}).get("values", {})
    context = {
        "matchId": point.match_id,
        "participantId": point.participant_id,
        "timestampMs": point.event_timestamp_ms,
        "mainItemOrdinal": point.main_item_ordinal,
    }
    threat: list[dict[str, Any]] = []
    enemies = values.get("enemy_champion_ids")
    if isinstance(enemies, list) and enemies:
        names = (
            await session.execute(
                select(ChampionModel.champion_key, ChampionModel.name).where(
                    ChampionModel.champion_key.in_(enemies)
                )
            )
        ).all()
        by_key = {key: name for key, name in names}
        known = [by_key[e] for e in enemies if e in by_key]
        if known:
            threat.append(
                {
                    "level": 1,
                    "text": "Observe o time inimigo: " + ", ".join(sorted(set(known))) + ".",
                    "source": "features.enemy_champion_ids, champions.name",
                }
            )
    extra: dict[str, Any] = {"context": context, "threatHints": threat}
    if row.status == "REVEALED":
        extra["historicalItemId"] = point.target_item_id
    return extra


async def create_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    body: dict[str, Any],
    idempotency_key: str,
    configs: Path,
) -> tuple[dict[str, Any], bool]:
    digest = request_hash(body)
    async with session_factory() as session:
        existing = await session.scalar(
            select(LearningAttemptModel).where(
                LearningAttemptModel.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if existing.request_sha256 != digest:
                raise ConflictError("IDEMPOTENCY_CONFLICT")
            return _public(existing, await _extra(session, existing)), False
        decision_id = uuid.UUID(str(body["decisionId"]))
        analysis = await _compute(session, decision_id, body, configs)
    attempt_id = uuid.uuid4()
    options = [
        {
            "itemId": c["itemId"],
            "name": c.get("name"),
            "remainingCost": c["remainingCost"],
            "tags": c.get("tags", []),
        }
        for c in analysis["candidates"]
    ]
    random.Random(attempt_id.int).shuffle(options)
    row = LearningAttemptModel(
        id=attempt_id,
        decision_id=decision_id,
        idempotency_key=idempotency_key,
        request_sha256=digest,
        objective=body["objective"],
        options=options,
        reason_codes=[] if options else analysis["support"]["reasonCodes"],
        status="OPEN",
        hint_level=0,
        skipped=False,
        exercise_version=EXERCISE_VERSION,
    )
    try:
        async with session_factory.begin() as session:
            session.add(row)
    except IntegrityError:
        return await create_attempt(session_factory, body, idempotency_key, configs)
    async with session_factory() as session:
        stored = await session.get(LearningAttemptModel, attempt_id)
        assert stored is not None
        return _public(stored, await _extra(session, stored)), True


async def _load(
    session: AsyncSession, attempt_id: uuid.UUID, *, lock: bool = False
) -> LearningAttemptModel:
    statement = select(LearningAttemptModel).where(LearningAttemptModel.id == attempt_id)
    row = await session.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise NotFoundError(f"tentativa {attempt_id} não encontrada")
    return row


async def get_attempt(
    session_factory: async_sessionmaker[AsyncSession], attempt_id: uuid.UUID, configs: Path
) -> dict[str, Any]:
    async with session_factory() as session:
        row = await _load(session, attempt_id)
        extra = await _extra(session, row)
        if row.status == "REVEALED" and row.analysis_id is not None:
            from riftlab.analyses.application import get_analysis

            extra["comparison"] = await get_analysis(session_factory, row.analysis_id)
        return _public(row, extra)


async def answer_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    attempt_id: uuid.UUID,
    body: dict[str, Any],
    configs: Path,
) -> dict[str, Any]:
    item, skipped = body.get("itemId"), bool(body.get("skipped"))
    rationale = body.get("rationale")
    async with session_factory.begin() as session:
        row = await _load(session, attempt_id, lock=True)
        same = (row.selected_item_id, row.skipped, row.rationale) == (item, skipped, rationale)
        if row.status != "OPEN":
            if row.status == "ANSWERED" and same:
                pass
            else:
                raise ConflictError("ATTEMPT_LOCKED")
        else:
            if not skipped and item not in {o["itemId"] for o in row.options}:
                raise InvalidRequestError("item fora das opções da tentativa")
            row.selected_item_id, row.skipped, row.rationale = (
                (None if skipped else item),
                skipped,
                rationale,
            )
            row.status = "ANSWERED"
    return await get_attempt(session_factory, attempt_id, configs)


async def hint_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    attempt_id: uuid.UUID,
    level: int,
    configs: Path,
) -> dict[str, Any]:
    async with session_factory.begin() as session:
        row = await _load(session, attempt_id, lock=True)
        row.hint_level = max(row.hint_level, level)
    return await get_attempt(session_factory, attempt_id, configs)


async def reveal_attempt(
    session_factory: async_sessionmaker[AsyncSession], attempt_id: uuid.UUID, configs: Path
) -> dict[str, Any]:
    async with session_factory() as session:
        row = await _load(session, attempt_id)
        body = {"decisionId": str(row.decision_id), "objective": row.objective}
    analysis, _ = await create_analysis(session_factory, body, f"attempt:{attempt_id}", configs)
    async with session_factory.begin() as session:
        row = await _load(session, attempt_id, lock=True)
        if row.status != "REVEALED":
            row.status = "REVEALED"
            row.analysis_id = uuid.UUID(analysis["analysisId"])
            row.revealed_at = datetime.now(UTC)
    return await get_attempt(session_factory, attempt_id, configs)
