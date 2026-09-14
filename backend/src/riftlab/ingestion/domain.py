from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from riftlab.shared.domain import SourceOrigin


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


@dataclass(frozen=True, slots=True)
class MatchParticipantSourceDTO:
    """Participante como a fonte bruta descreve, anterior à validação/persistência
    (CC-201). Sem identidade pessoal: fontes demo não têm PUUID real algum."""

    participant_id: int
    champion_id: int
    team: int
    role: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineFrameDTO:
    """Snapshot bruto de um instante da partida. participant_frames fica opaco
    (dict por participant_id) — reconstrução real de inventário/ouro é CC-201/204.
    Um dicionário vazio preserva ausência de dado, nunca é preenchido por
    aproximação."""

    timestamp_ms: int
    participant_frames: dict[int, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TimelineEventDTO:
    """source_index preserva a posição original do provedor (ordem de frames e,
    dentro de cada frame, ordem de eventos). Eventos idênticos ou com o mesmo
    timestamp_ms nunca são deduplicados ou reordenados pelo adapter."""

    source_index: int
    timestamp_ms: int
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MatchSourceResult:
    """Retorno bruto de MatchSource, anterior a qualquer validação/reconstrução/
    persistência (essas etapas pertencem a CC-201/203/204). origin=CURATED_DEMO é
    a única origem implementada nesta etapa."""

    match_id: str
    origin: SourceOrigin
    schema_version: str
    raw_hash: str
    queue: int
    map_id: int
    patch: str
    start_at: datetime
    duration_seconds: int
    participants: tuple[MatchParticipantSourceDTO, ...]
    frames: tuple[TimelineFrameDTO, ...]
    events: tuple[TimelineEventDTO, ...]


class MatchSource(Protocol):
    """Fonte plugável de dados de partida. Implementações não escolhem uma fonte
    real (ex.: Riot ao vivo) automaticamente na ausência de configuração — isso
    fica para CC-105/201; nesta etapa só existe DemoMatchSource."""

    async def load_match(self, match_id: str) -> MatchSourceResult:
        """Levanta SourceNotFoundError para identificador fora do escopo da fonte
        e SourceSchemaError para JSON inválido, schema desconhecido ou campo
        estrutural obrigatório ausente. Nunca retorna None nem interpola dado
        ausente."""
        ...
