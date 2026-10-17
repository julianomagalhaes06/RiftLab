"""Linha do tempo de uma partida importada para a tela de análise: compras e vendas por
participante (desfazer aplicado), subidas de habilidade, abates, objetivos e ouro por
quadro. Só banco, em transação READ ONLY; nada pessoal sai daqui (ids de participante
1..10 e de item, apenas)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.classification import ItemClassificationTable, classify_catalog
from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import read_only_unit_of_work
from riftlab.shared.errors import NotFoundError
from riftlab.shared.numeric import is_ascii_digits
from riftlab.timeline.models import MatchFramesModel, MatchReconstructionModel, TimelineEventModel

SessionFactory = async_sessionmaker[AsyncSession]

_TYPES = (
    "ITEM_PURCHASED",
    "ITEM_SOLD",
    "ITEM_UNDO",
    "SKILL_LEVEL_UP",
    "CHAMPION_KILL",
    "ELITE_MONSTER_KILL",
    "BUILDING_KILL",
)

_MONSTERS = {
    "DRAGON": "DRAGON",
    "BARON_NASHOR": "BARON",
    "RIFTHERALD": "HERALD",
    "HORDE": "VOIDGRUB",
    "ATAKHAN": "ATAKHAN",
}
_BUILDINGS = {"TOWER_BUILDING": "TOWER", "INHIBITOR_BUILDING": "INHIBITOR"}


@dataclass(frozen=True, slots=True)
class RawEvent:
    timestamp_ms: int
    type: str
    payload: Mapping[str, Any]


@dataclass(slots=True)
class ItemEvent:
    timestamp_ms: int
    item_id: int
    kind: str  # PURCHASE | SOLD
    item_class: str | None = None


@dataclass(frozen=True, slots=True)
class SkillUp:
    timestamp_ms: int
    skill_slot: int
    level_up_type: str


@dataclass(slots=True)
class ParticipantTimeline:
    participant_id: int
    items: list[ItemEvent] = field(default_factory=list)
    skills: list[SkillUp] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Kill:
    timestamp_ms: int
    killer_id: int
    victim_id: int
    assisting_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Objective:
    timestamp_ms: int
    kind: str
    team: int | None
    sub_type: str | None


@dataclass(frozen=True, slots=True)
class GoldFrame:
    timestamp_ms: int
    gold: Mapping[int, int]


@dataclass(frozen=True, slots=True)
class MatchTimelineData:
    match_id: str
    data_dragon_version: str | None
    participants: list[ParticipantTimeline]
    kills: list[Kill]
    objectives: list[Objective]
    gold_frames: list[GoldFrame] | None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _undo(items: list[ItemEvent], kind: str, item_id: int) -> None:
    for index in range(len(items) - 1, -1, -1):
        if items[index].kind == kind and items[index].item_id == item_id:
            del items[index]
            return


def build_timeline(
    events: Iterable[RawEvent],
    participant_ids: Sequence[int],
    teams: Mapping[int, int],
    classification: ItemClassificationTable | None = None,
) -> tuple[list[ParticipantTimeline], list[Kill], list[Objective]]:
    """Puro. Eventos em ordem de timestamp. ITEM_UNDO desfaz a última compra
    (`beforeId`) ou venda (`afterId`) do mesmo item; participante 0 (sistema) e ids
    fora da partida são ignorados."""
    by_id = {pid: ParticipantTimeline(pid) for pid in participant_ids}
    kills: list[Kill] = []
    objectives: list[Objective] = []
    for event in events:
        payload = event.payload
        pid = _int(payload.get("participantId"))
        if event.type in ("ITEM_PURCHASED", "ITEM_SOLD") and pid in by_id:
            item_id = _int(payload.get("itemId"))
            if item_id and item_id > 0:
                item_class = (
                    classification.classify(item_id).item_class.value
                    if classification is not None
                    else None
                )
                kind = "PURCHASE" if event.type == "ITEM_PURCHASED" else "SOLD"
                by_id[pid].items.append(ItemEvent(event.timestamp_ms, item_id, kind, item_class))
        elif event.type == "ITEM_UNDO" and pid in by_id:
            before, after = _int(payload.get("beforeId")) or 0, _int(payload.get("afterId")) or 0
            if before > 0:
                _undo(by_id[pid].items, "PURCHASE", before)
            elif after > 0:
                _undo(by_id[pid].items, "SOLD", after)
        elif event.type == "SKILL_LEVEL_UP" and pid in by_id:
            slot = _int(payload.get("skillSlot"))
            if slot is not None and 1 <= slot <= 4:
                level_type = payload.get("levelUpType")
                by_id[pid].skills.append(
                    SkillUp(
                        event.timestamp_ms,
                        slot,
                        level_type if isinstance(level_type, str) else "NORMAL",
                    )
                )
        elif event.type == "CHAMPION_KILL":
            victim = _int(payload.get("victimId"))
            if victim in by_id:
                assisting = payload.get("assistingParticipantIds")
                kills.append(
                    Kill(
                        event.timestamp_ms,
                        _int(payload.get("killerId")) or 0,
                        victim,
                        tuple(
                            a for a in (assisting if isinstance(assisting, list) else ()) if _int(a)
                        ),
                    )
                )
        elif event.type == "ELITE_MONSTER_KILL":
            kind = _MONSTERS.get(str(payload.get("monsterType")), "OTHER")
            sub_type = payload.get("monsterSubType")
            team = _int(payload.get("killerTeamId"))
            if team is None:
                team = teams.get(_int(payload.get("killerId")) or 0)
            objectives.append(
                Objective(
                    event.timestamp_ms,
                    kind,
                    team,
                    sub_type if isinstance(sub_type, str) else None,
                )
            )
        elif event.type == "BUILDING_KILL":
            kind = _BUILDINGS.get(str(payload.get("buildingType")), "OTHER")
            owner = _int(payload.get("teamId"))  # time DONO da estrutura destruída
            tower_type = payload.get("towerType")
            objectives.append(
                Objective(
                    event.timestamp_ms,
                    kind,
                    {100: 200, 200: 100}.get(owner) if owner is not None else None,
                    tower_type if isinstance(tower_type, str) else None,
                )
            )
    return [by_id[pid] for pid in participant_ids], kills, objectives


def parse_gold_frames(document: Any) -> list[GoldFrame]:
    frames: list[GoldFrame] = []
    for frame in document if isinstance(document, list) else ():
        if not isinstance(frame, dict) or _int(frame.get("t")) is None:
            continue
        gold = frame.get("gold")
        frames.append(
            GoldFrame(
                frame["t"],
                {
                    int(pid): value
                    for pid, value in (gold.items() if isinstance(gold, dict) else ())
                    if is_ascii_digits(str(pid)) and _int(value) is not None
                },
            )
        )
    return frames


async def _classification(
    session: AsyncSession, match_id: str
) -> tuple[ItemClassificationTable | None, str | None]:
    catalog_id: uuid.UUID | None = await session.scalar(
        select(MatchReconstructionModel.catalog_id)
        .where(
            MatchReconstructionModel.match_id == match_id,
            MatchReconstructionModel.catalog_id.is_not(None),
        )
        .order_by(
            MatchReconstructionModel.reconstruction_version.desc(),
            MatchReconstructionModel.created_at.desc(),
        )
        .limit(1)
    )
    if catalog_id is None:
        return None, None
    repo = SqlAlchemyCatalogRepository(session)
    catalog = await repo.get_catalog_version(catalog_id)
    if catalog is None:
        return None, None
    items = {item.item_id: item for item in await repo.list_items(catalog_id)}
    excluded = frozenset(e.item_id for e in await repo.list_excluded_items(catalog_id))
    table = classify_catalog(
        items, curation_rules_version=catalog.curation_rules_version, excluded_item_ids=excluded
    )
    return table, catalog.data_dragon_version


async def get_match_timeline(session_factory: SessionFactory, match_id: str) -> MatchTimelineData:
    async with read_only_unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        if await repo.get_match(match_id) is None:
            raise NotFoundError(f"match {match_id!r} não encontrado")
        participants = await repo.list_participants(match_id)
        rows = await session.execute(
            select(
                TimelineEventModel.timestamp_ms, TimelineEventModel.type, TimelineEventModel.payload
            )
            .where(TimelineEventModel.match_id == match_id, TimelineEventModel.type.in_(_TYPES))
            .order_by(TimelineEventModel.timestamp_ms, TimelineEventModel.source_index)
        )
        events = [RawEvent(t, kind, payload or {}) for t, kind, payload in rows.tuples()]
        classification, dd_version = await _classification(session, match_id)
        frames_row = await session.get(MatchFramesModel, match_id)
    timelines, kills, objectives = build_timeline(
        events,
        [p.participant_id for p in participants],
        {p.participant_id: p.team for p in participants},
        classification,
    )
    return MatchTimelineData(
        match_id,
        dd_version,
        timelines,
        kills,
        objectives,
        parse_gold_frames(frames_row.frames) if frames_row is not None else None,
    )
