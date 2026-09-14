from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

CURRENT_DECISION_STATE_SCHEMA_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    """Mesmo timestamp_ms com source_index diferente é válido: a ordem original
    do provedor é preservada pelo source_index, não recalculada."""

    match_id: str
    source_index: int
    timestamp_ms: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "v1"


@dataclass(frozen=True, slots=True)
class DecisionState:
    """Estado imediatamente anterior a uma decisão. catalog_id é opcional: um
    estado incompatível pode ser registrado sem catálogo disponível, sem que
    isso vire um zero disfarçado de feature."""

    id: uuid.UUID
    match_id: str
    participant_id: int
    event_source_index: int
    state_version: int = 1
    snapshot: dict[str, Any] = field(default_factory=dict)
    features: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CURRENT_DECISION_STATE_SCHEMA_VERSION
    catalog_id: uuid.UUID | None = None
    created_at: datetime | None = None


class TimelineRepository(Protocol):
    async def add_event(self, event: TimelineEvent) -> None: ...

    async def get_event(self, match_id: str, source_index: int) -> TimelineEvent | None: ...

    async def list_events(self, match_id: str) -> list[TimelineEvent]: ...

    async def add_decision_state(self, state: DecisionState) -> None: ...

    async def get_decision_state(self, state_id: uuid.UUID) -> DecisionState | None: ...

    async def list_decision_states(
        self, match_id: str, participant_id: int
    ) -> list[DecisionState]: ...
