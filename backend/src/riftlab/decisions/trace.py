"""Da Riot até a análise: o caminho do dado oficial até uma decisão de compra.

Para uma decisão, devolve o evento da linha do tempo da Riot (Match-V5) que a originou, como
veio da API, os eventos de item e de nível do mesmo jogador nos 2 minutos anteriores, o quadro
de ouro e nível anterior à compra e quantos eventos de item, desde o início da partida, refazem
o inventário. Só leitura no banco; os eventos entram só com campos do próprio jogador (ID do
participante, item, nível, ouro devolvido), sem PUUID nem Riot ID.

Modo Estudo: nada aqui revela a compra. O evento da decisão vem sem o item (`hidden`), e só
entram eventos de antes do instante da compra; os componentes consumidos no mesmo instante
denunciariam o item comprado.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.decisions.models import DecisionPointModel
from riftlab.ingestion.models import MatchModel
from riftlab.shared.db import read_only_unit_of_work
from riftlab.shared.errors import NotFoundError
from riftlab.timeline.models import MatchFramesModel, TimelineEventModel

SessionFactory = async_sessionmaker[AsyncSession]
ITEM_TYPES = ("ITEM_PURCHASED", "ITEM_SOLD", "ITEM_DESTROYED", "ITEM_UNDO")
TRACE_TYPES = (*ITEM_TYPES, "LEVEL_UP")
BEFORE_MS = 120_000
NEARBY_LIMIT = 24
# Campos que identificariam o item comprado no evento da própria decisão.
HIDDEN_FIELDS = ("itemId", "beforeId", "afterId", "goldGain")
# Campos do evento mostrados como vieram da Riot: só os do próprio jogador.
RAW_FIELDS = ("participantId", "itemId", "beforeId", "afterId", "goldGain", "level")


def _event(
    row: TimelineEventModel, *, hide_item: bool = False, names: dict[int, str | None] | None = None
) -> dict[str, Any]:
    payload = row.payload or {}
    hidden = HIDDEN_FIELDS if hide_item else ()
    raw: dict[str, Any] = {"type": row.type, "timestamp": row.timestamp_ms}
    raw.update({key: payload[key] for key in RAW_FIELDS if key in payload and key not in hidden})
    item = None if hide_item else payload.get("itemId")
    if row.type == "ITEM_UNDO" and not hide_item:
        item = payload.get("beforeId") or payload.get("afterId")
    item_id = item if isinstance(item, int) and item > 0 else None
    return {
        "sourceIndex": row.source_index,
        "timestampMs": row.timestamp_ms,
        "type": row.type,
        "itemId": item_id,
        "itemName": (names or {}).get(item_id) if item_id is not None else None,
        "level": payload.get("level") if isinstance(payload.get("level"), int) else None,
        "hidden": list(field for field in hidden if field in payload),
        "raw": raw,
    }


def _frame(frames: list[dict[str, Any]] | None, participant: int, at_ms: int) -> dict[str, Any]:
    """Último quadro até a compra; a Riot grava um por minuto."""
    if not frames:
        return {"frame": None, "frameCount": 0}
    before = [frame for frame in frames if isinstance(frame.get("t"), int) and frame["t"] <= at_ms]
    if not before:
        return {"frame": None, "frameCount": len(frames)}
    frame = before[-1]
    key = str(participant)

    def value(name: str) -> int | None:
        found = (frame.get(name) or {}).get(key)
        return found if isinstance(found, int) else None

    return {
        "frame": {
            "timestampMs": frame["t"],
            "gold": value("gold"),
            "level": value("level"),
            "cs": value("cs"),
            "xp": value("xp"),
        },
        "frameCount": len(frames),
    }


async def decision_trace(factory: SessionFactory, decision_id: uuid.UUID) -> dict[str, Any]:
    async with read_only_unit_of_work(factory) as session:
        point = await session.get(DecisionPointModel, decision_id)
        if point is None:
            raise NotFoundError(f"decisão {decision_id} não encontrada")
        match = await session.get(MatchModel, point.match_id)
        assert match is not None
        participant = TimelineEventModel.payload["participantId"].astext.cast(Integer)
        own = participant == point.participant_id
        in_match = TimelineEventModel.match_id == point.match_id
        event = await session.get(TimelineEventModel, (point.match_id, point.event_source_index))
        total = await session.scalar(select(func.count()).where(in_match))
        # Só o que veio antes do instante da compra (ver o docstring do módulo).
        before_decision = TimelineEventModel.timestamp_ms < point.event_timestamp_ms
        item_events = await session.scalar(
            select(func.count()).where(
                in_match, own, TimelineEventModel.type.in_(ITEM_TYPES), before_decision
            )
        )
        nearby = (
            await session.scalars(
                select(TimelineEventModel)
                .where(
                    in_match,
                    own,
                    TimelineEventModel.type.in_(TRACE_TYPES),
                    TimelineEventModel.timestamp_ms >= point.event_timestamp_ms - BEFORE_MS,
                    before_decision,
                )
                .order_by(
                    TimelineEventModel.timestamp_ms.desc(), TimelineEventModel.source_index.desc()
                )
                .limit(NEARBY_LIMIT)
            )
        ).all()
        frames = await session.get(MatchFramesModel, point.match_id)
        # Nomes do mesmo catálogo fixado pela decisão (como o inventário do detalhe).
        names: dict[int, str | None] = {}
        if point.catalog_id is not None:
            catalog = SqlAlchemyCatalogRepository(session)
            for row in nearby:
                item = _event(row)["itemId"]
                if item is None or item in names:
                    continue
                found = await catalog.get_item(point.catalog_id, item)
                name = found.attributes.get("name") if found is not None else None
                names[item] = name if isinstance(name, str) and name else None
    rows = list(reversed(nearby))  # os mais próximos da compra, em ordem de tempo
    return {
        "decisionId": point.id,
        "matchId": point.match_id,
        "participantId": point.participant_id,
        "patch": match.patch,
        "queue": match.queue,
        "timelineEvents": int(total or 0),
        "participantItemEvents": int(item_events or 0),
        "event": _event(event, hide_item=True) if event is not None else None,
        "nearby": [_event(row, names=names) for row in rows],
        **_frame(frames.frames if frames else None, point.participant_id, point.event_timestamp_ms),
    }
