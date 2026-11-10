"""CC-304: study attempts — choice before reveal (docs/learning-design.md).

Options are the engine's top three legal candidates, shown in a neutral order that
is stable per attempt (shuffled with the attempt id as seed); before REVEALED the
public state carries no rank, score, cost or historical purchase. Hints only show
available facts. Reveal creates the analysis idempotently (one per attempt) and is
monotonic. Answers lock after the first one; a new choice needs a new attempt.
"""

from __future__ import annotations

import base64
import binascii
import json
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Uuid, literal, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.analyses.application import _compute, create_analysis, request_hash
from riftlab.champions.models import ChampionModel
from riftlab.decisions.models import DecisionPointModel
from riftlab.learning.models import LearningAttemptModel
from riftlab.shared.errors import (
    ConflictError,
    InvalidCursorError,
    InvalidRequestError,
    NotFoundError,
)

EXERCISE_VERSION = "study_v1"
ATTEMPTS_CURSOR_VERSION = 1
ATTEMPTS_MAX_CURSOR_CHARS = 512


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
        "mode": row.mode,
        "reflection": row.reflection,
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
    *,
    owner: uuid.UUID | None = None,
) -> tuple[dict[str, Any], bool]:
    """`owner`: usuário da sessão (Sprint 2, #11); sem sessão, tentativa sem dono."""
    digest = request_hash(body)
    async with session_factory() as session:
        existing = await session.scalar(
            select(LearningAttemptModel).where(
                LearningAttemptModel.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if existing.request_sha256 != digest or existing.user_id != owner:
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
        mode=body.get("mode") or "STUDY",
        user_id=owner,
    )
    try:
        async with session_factory.begin() as session:
            session.add(row)
    except IntegrityError:
        return await create_attempt(session_factory, body, idempotency_key, configs, owner=owner)
    async with session_factory() as session:
        stored = await session.get(LearningAttemptModel, attempt_id)
        assert stored is not None
        return _public(stored, await _extra(session, stored)), True


async def _load(
    session: AsyncSession,
    attempt_id: uuid.UUID,
    owner: uuid.UUID | None,
    *,
    lock: bool = False,
) -> LearningAttemptModel:
    """Tentativa de outro usuário (ou com dono, pedida sem sessão) responde como inexistente."""
    statement = select(LearningAttemptModel).where(
        LearningAttemptModel.id == attempt_id,
        LearningAttemptModel.user_id.is_not_distinct_from(owner),
    )
    row = await session.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        raise NotFoundError(f"tentativa {attempt_id} não encontrada")
    return row


async def get_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    attempt_id: uuid.UUID,
    configs: Path,
    *,
    owner: uuid.UUID | None = None,
) -> dict[str, Any]:
    async with session_factory() as session:
        row = await _load(session, attempt_id, owner)
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
    *,
    owner: uuid.UUID | None = None,
) -> dict[str, Any]:
    item, skipped = body.get("itemId"), bool(body.get("skipped"))
    rationale = body.get("rationale")
    async with session_factory.begin() as session:
        row = await _load(session, attempt_id, owner, lock=True)
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
    return await get_attempt(session_factory, attempt_id, configs, owner=owner)


async def hint_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    attempt_id: uuid.UUID,
    level: int,
    configs: Path,
    *,
    owner: uuid.UUID | None = None,
) -> dict[str, Any]:
    async with session_factory.begin() as session:
        row = await _load(session, attempt_id, owner, lock=True)
        if row.mode == "TRANSFER":
            raise ConflictError("HINTS_DISABLED")
        row.hint_level = max(row.hint_level, level)
    return await get_attempt(session_factory, attempt_id, configs, owner=owner)


async def reveal_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    attempt_id: uuid.UUID,
    configs: Path,
    *,
    owner: uuid.UUID | None = None,
) -> dict[str, Any]:
    async with session_factory() as session:
        row = await _load(session, attempt_id, owner)
        body = {"decisionId": str(row.decision_id), "objective": row.objective}
    analysis, _ = await create_analysis(session_factory, body, f"attempt:{attempt_id}", configs)
    async with session_factory.begin() as session:
        row = await _load(session, attempt_id, owner, lock=True)
        if row.status != "REVEALED":
            row.status = "REVEALED"
            row.analysis_id = uuid.UUID(analysis["analysisId"])
            row.revealed_at = datetime.now(UTC)
    return await get_attempt(session_factory, attempt_id, configs, owner=owner)


async def reflect_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    attempt_id: uuid.UUID,
    reflection: str,
    configs: Path,
    *,
    owner: uuid.UUID | None = None,
) -> dict[str, Any]:
    """CC-505: one reflection after the comparison; the same text again is idempotent."""
    async with session_factory.begin() as session:
        row = await _load(session, attempt_id, owner, lock=True)
        if row.status != "REVEALED":
            raise ConflictError("ATTEMPT_NOT_REVEALED")
        if row.reflection is None:
            row.reflection = reflection
            row.reflected_at = datetime.now(UTC)
        elif row.reflection != reflection:
            raise ConflictError("ATTEMPT_LOCKED")
    return await get_attempt(session_factory, attempt_id, configs, owner=owner)


async def progress(
    session_factory: async_sessionmaker[AsyncSession], *, owner: uuid.UUID | None = None
) -> dict[str, Any]:
    """CC-505: local, descriptive counts. No score, ranking or skill estimate. Só as
    tentativas do usuário da sessão (#11); sem sessão, as sem dono."""
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(LearningAttemptModel).where(
                        LearningAttemptModel.user_id.is_not_distinct_from(owner)
                    )
                )
            )
            .scalars()
            .all()
        )

    def count(mode: str) -> dict[str, int]:
        part = [r for r in rows if r.mode == mode]
        return {
            "attempts": len(part),
            "answered": sum(r.status != "OPEN" and not r.skipped for r in part),
            "skipped": sum(r.skipped for r in part),
            "revealed": sum(r.status == "REVEALED" for r in part),
            "usedHints": sum(r.hint_level > 0 for r in part),
            "reflections": sum(r.reflection is not None for r in part),
        }

    return {"study": count("STUDY"), "transfer": count("TRANSFER")}


@dataclass(frozen=True, slots=True)
class AttemptsCursorPosition:
    """Posição de retomada da listagem, vinculada ao dono (owner)."""

    created_at_ms: int
    attempt_id: uuid.UUID


def _owner_key(owner: uuid.UUID | None) -> str:
    return str(owner) if owner is not None else ""


def encode_attempts_cursor(position: AttemptsCursorPosition, *, owner: uuid.UUID | None) -> str:
    """Cursor OPACO: base64url de JSON versionado (mesma técnica de
    decisions/queries.py). Não é assinado: adulterar só troca o ponto de
    retomada dentro do MESMO escopo já autorizado (o dono da sessão); o resto é
    validado estritamente na leitura."""
    payload = {
        "v": ATTEMPTS_CURSOR_VERSION,
        "o": _owner_key(owner),
        "t": position.created_at_ms,
        "i": str(position.attempt_id),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_attempts_cursor(raw: str, *, owner: uuid.UUID | None) -> AttemptsCursorPosition:
    """Qualquer defeito (base64, JSON, chaves, tipos, escopo) levanta
    InvalidCursorError com texto fixo: o conteúdo recebido nunca é ecoado."""
    if not raw or len(raw) > ATTEMPTS_MAX_CURSOR_CHARS or not raw.isascii():
        raise InvalidCursorError("cursor inválido")
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise InvalidCursorError("cursor inválido") from exc
    if not isinstance(payload, dict) or set(payload) != {"v", "o", "t", "i"}:
        raise InvalidCursorError("cursor inválido")
    if payload["v"] != ATTEMPTS_CURSOR_VERSION or isinstance(payload["v"], bool):
        raise InvalidCursorError("cursor inválido")
    if payload["o"] != _owner_key(owner):
        raise InvalidCursorError("cursor inválido")  # de outro dono
    if not isinstance(payload["t"], int) or isinstance(payload["t"], bool) or payload["t"] < 0:
        raise InvalidCursorError("cursor inválido")
    if not isinstance(payload["i"], str):
        raise InvalidCursorError("cursor inválido")
    try:
        attempt_id = uuid.UUID(payload["i"])
    except ValueError as exc:
        raise InvalidCursorError("cursor inválido") from exc
    if str(attempt_id) != payload["i"]:  # só a forma canônica
        raise InvalidCursorError("cursor inválido")
    return AttemptsCursorPosition(created_at_ms=payload["t"], attempt_id=attempt_id)


@dataclass(frozen=True, slots=True)
class AttemptSummaryRow:
    attempt_id: uuid.UUID
    decision_id: uuid.UUID
    match_id: str
    participant_id: int
    status: str
    mode: str
    skipped: bool
    has_reflection: bool
    analysis_id: uuid.UUID | None
    created_at: datetime
    revealed_at: datetime | None


@dataclass(frozen=True, slots=True)
class AttemptsPage:
    items: list[AttemptSummaryRow]
    next_cursor: str | None = None


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


async def list_attempts(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: uuid.UUID | None,
    limit: int,
    cursor: str | None,
) -> AttemptsPage:
    """CC-505: histórico das próprias tentativas (biblioteca de decisões, minha
    evolução), mais recente primeiro. Só metadados de localização/tempo — o
    conteúdo pedagógico continua atrás de GET /{attemptId}."""
    position = decode_attempts_cursor(cursor, owner=owner) if cursor else None
    async with session_factory() as session:
        stmt = (
            select(
                LearningAttemptModel.id,
                LearningAttemptModel.decision_id,
                DecisionPointModel.match_id,
                DecisionPointModel.participant_id,
                LearningAttemptModel.status,
                LearningAttemptModel.mode,
                LearningAttemptModel.skipped,
                LearningAttemptModel.reflection,
                LearningAttemptModel.analysis_id,
                LearningAttemptModel.created_at,
                LearningAttemptModel.revealed_at,
            )
            .join(DecisionPointModel, LearningAttemptModel.decision_id == DecisionPointModel.id)
            .where(LearningAttemptModel.user_id.is_not_distinct_from(owner))
        )
        if position is not None:
            cursor_created_at = datetime.fromtimestamp(position.created_at_ms / 1000, tz=UTC)
            stmt = stmt.where(
                tuple_(LearningAttemptModel.created_at, LearningAttemptModel.id)
                < tuple_(
                    literal(cursor_created_at, DateTime(timezone=True)),
                    literal(position.attempt_id, Uuid),
                )
            )
        stmt = stmt.order_by(
            LearningAttemptModel.created_at.desc(), LearningAttemptModel.id.desc()
        ).limit(limit + 1)
        rows = (await session.execute(stmt)).all()

    items = [
        AttemptSummaryRow(
            attempt_id=row.id,
            decision_id=row.decision_id,
            match_id=row.match_id,
            participant_id=row.participant_id,
            status=row.status,
            mode=row.mode,
            skipped=row.skipped,
            has_reflection=row.reflection is not None,
            analysis_id=row.analysis_id,
            created_at=row.created_at,
            revealed_at=row.revealed_at,
        )
        for row in rows[:limit]
    ]
    next_cursor: str | None = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = encode_attempts_cursor(
            AttemptsCursorPosition(created_at_ms=_ms(last.created_at), attempt_id=last.attempt_id),
            owner=owner,
        )
    return AttemptsPage(items=items, next_cursor=next_cursor)
