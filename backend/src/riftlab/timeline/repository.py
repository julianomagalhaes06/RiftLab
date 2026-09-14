from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.timeline.domain import DecisionState, TimelineEvent
from riftlab.timeline.models import DecisionStateModel, TimelineEventModel


class SqlAlchemyTimelineRepository:
    """Implementa TimelineRepository. Não faz commit: a UoW chamadora decide o
    limite da transação."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_event(self, event: TimelineEvent) -> None:
        self._session.add(
            TimelineEventModel(
                match_id=event.match_id,
                source_index=event.source_index,
                timestamp_ms=event.timestamp_ms,
                type=event.type,
                payload=event.payload,
                schema_version=event.schema_version,
            )
        )

    async def get_event(self, match_id: str, source_index: int) -> TimelineEvent | None:
        row = await self._session.get(
            TimelineEventModel, {"match_id": match_id, "source_index": source_index}
        )
        if row is None:
            return None
        return _event_from_row(row)

    async def list_events(self, match_id: str) -> list[TimelineEvent]:
        stmt = (
            select(TimelineEventModel)
            .where(TimelineEventModel.match_id == match_id)
            .order_by(TimelineEventModel.timestamp_ms, TimelineEventModel.source_index)
        )
        result = await self._session.execute(stmt)
        return [_event_from_row(row) for row in result.scalars()]

    async def add_decision_state(self, state: DecisionState) -> None:
        self._session.add(
            DecisionStateModel(
                id=state.id,
                match_id=state.match_id,
                participant_id=state.participant_id,
                event_source_index=state.event_source_index,
                state_version=state.state_version,
                snapshot=state.snapshot,
                features=state.features,
                quality=state.quality,
                schema_version=state.schema_version,
                catalog_id=state.catalog_id,
            )
        )

    async def get_decision_state(self, state_id: uuid.UUID) -> DecisionState | None:
        row = await self._session.get(DecisionStateModel, state_id)
        if row is None:
            return None
        return _decision_state_from_row(row)

    async def list_decision_states(self, match_id: str, participant_id: int) -> list[DecisionState]:
        stmt = (
            select(DecisionStateModel)
            .where(
                DecisionStateModel.match_id == match_id,
                DecisionStateModel.participant_id == participant_id,
            )
            .order_by(DecisionStateModel.event_source_index, DecisionStateModel.state_version)
        )
        result = await self._session.execute(stmt)
        return [_decision_state_from_row(row) for row in result.scalars()]


def _event_from_row(row: TimelineEventModel) -> TimelineEvent:
    return TimelineEvent(
        match_id=row.match_id,
        source_index=row.source_index,
        timestamp_ms=row.timestamp_ms,
        type=row.type,
        payload=row.payload,
        schema_version=row.schema_version,
    )


def _decision_state_from_row(row: DecisionStateModel) -> DecisionState:
    return DecisionState(
        id=row.id,
        match_id=row.match_id,
        participant_id=row.participant_id,
        event_source_index=row.event_source_index,
        state_version=row.state_version,
        snapshot=row.snapshot,
        features=row.features,
        quality=row.quality,
        schema_version=row.schema_version,
        catalog_id=row.catalog_id,
        created_at=row.created_at,
    )
