from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class ImportMode(StrEnum):
    DEMO = "DEMO"
    RIOT = "RIOT"


class ImportJobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


ACTIVE_IMPORT_JOB_STATES: frozenset[ImportJobState] = frozenset(
    {ImportJobState.QUEUED, ImportJobState.RUNNING, ImportJobState.RETRY_WAIT}
)


@dataclass(frozen=True, slots=True)
class Match:
    """Importação repetida do mesmo match_id não cria outra partida (PK natural)."""

    match_id: str
    queue: int
    map_id: int
    patch: str
    start_at: datetime
    duration_seconds: int
    raw_hash: str


@dataclass(frozen=True, slots=True)
class Participant:
    match_id: str
    participant_id: int
    champion_id: int
    team: int
    subject_hash: str
    role: str | None = None


@dataclass(frozen=True, slots=True)
class ImportJob:
    """requested_match_id nunca tem FK obrigatória para matches: o job precede a
    importação. match_id só é preenchido depois que a partida existe de fato."""

    id: uuid.UUID
    requested_match_id: str
    mode: ImportMode
    state: ImportJobState
    attempts: int = 0
    match_id: str | None = None
    lease_until: datetime | None = None
    retry_at: datetime | None = None
    error_code: str | None = None


class IngestionRepository(Protocol):
    async def add_match(self, match: Match) -> None: ...

    async def add_participant(self, participant: Participant) -> None: ...

    async def create_import_job(self, job: ImportJob) -> None: ...

    async def get_match(self, match_id: str) -> Match | None: ...

    async def get_participant(self, match_id: str, participant_id: int) -> Participant | None: ...

    async def get_import_job(self, job_id: uuid.UUID) -> ImportJob | None: ...

    async def list_active_import_jobs(
        self, requested_match_id: str, mode: ImportMode
    ) -> list[ImportJob]: ...
