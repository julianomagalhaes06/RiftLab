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

# docs/architecture.md: "cinco tentativas transitórias com backoff exponencial/
# jitter e teto 15 min". attempts conta cada vez que o job é entregue a um
# worker (reserva fresca, retomada de RETRY_WAIT ou reclamação de lease
# vencido) — contar a reclamação evita loop infinito num job que sempre
# derruba o worker (CC-201).
MAX_TRANSIENT_ATTEMPTS = 5
LEASE_SECONDS = 120
LEASE_HEARTBEAT_SECONDS = 30


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
    importação. match_id só é preenchido depois que a partida existe de fato.
    lease_generation é o token de posse (CC-201): quem reservou o job guarda o
    valor retornado e o usa em toda escrita final subsequente."""

    id: uuid.UUID
    requested_match_id: str
    mode: ImportMode
    state: ImportJobState
    attempts: int = 0
    match_id: str | None = None
    lease_until: datetime | None = None
    lease_generation: int = 0
    retry_at: datetime | None = None
    error_code: str | None = None
    retry_of_job_id: uuid.UUID | None = None


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

    async def get_latest_import_job(
        self, requested_match_id: str, mode: ImportMode
    ) -> ImportJob | None:
        """Job mais recente (qualquer estado) para a identidade — usado por
        POST /imports para reaproveitar um job ativo ou já SUCCEEDED sem
        duplicar partida (CC-201)."""
        ...

    async def reserve_next_job(self, now: datetime) -> ImportJob | None:
        """SELECT ... FOR UPDATE SKIP LOCKED numa transação curta: reserva o
        próximo job elegível (QUEUED, RETRY_WAIT devido ou RUNNING com lease
        vencido), incrementa lease_generation e devolve o job com o novo
        token de posse. Jobs que já esgotaram MAX_TRANSIENT_ATTEMPTS são
        marcados FAILED nesta mesma transação em vez de reservados de novo
        (evita loop infinito). None se não há trabalho elegível agora."""
        ...

    async def renew_lease(self, job_id: uuid.UUID, generation: int, now: datetime) -> bool:
        """Heartbeat: estende lease_until só se generation ainda bate (posse
        preservada). False se a posse já mudou de mãos."""
        ...

    async def mark_import_succeeded(
        self, job_id: uuid.UUID, generation: int, match_id: str
    ) -> bool:
        """Guardado por generation: um worker que perdeu a posse não pode
        finalizar um job já reclamado por outro. False se a escrita foi
        rejeitada (posse perdida) — o chamador nunca trata isso como erro a
        propagar, só descarta o resultado."""
        ...

    async def mark_import_failed(self, job_id: uuid.UUID, generation: int, error_code: str) -> bool:
        """Falha permanente (401/403/404/schema incompatível/tentativas
        esgotadas). Guardado por generation."""
        ...

    async def mark_import_retry_wait(
        self, job_id: uuid.UUID, generation: int, retry_at: datetime, error_code: str
    ) -> bool:
        """Falha transitória (timeout/5xx/429): libera a lease e agenda
        retry_at durável, sem manter transação/rede pendente. Guardado por
        generation."""
        ...

    async def get_riot_cooldown_until(self) -> datetime | None:
        """Cooldown compartilhado entre TODOS os jobs RIOT (não por job),
        durável entre reinícios do worker — 429 numa chamada real pausa a
        fila inteira até este instante, não só o job que recebeu o 429
        (CC-201, achado real da revisão: cada job tinha seu próprio
        retry_at isolado, a fila continuava chamando a API)."""
        ...

    async def extend_riot_cooldown_until(self, until: datetime) -> None:
        """Só estende — nunca encurta um cooldown existente mais longo
        (defesa contra um 429 mais antigo processado fora de ordem
        reduzir uma espera já em vigor)."""
        ...


@dataclass(frozen=True, slots=True)
class MatchParticipantSourceDTO:
    """Participante como a fonte bruta descreve, anterior à validação/persistência
    (CC-201). subject_hash já sai hasheado da fonte — nenhum identificador
    pessoal bruto (PUUID/Riot ID) chega a existir neste DTO nem em qualquer
    camada além do adapter que o calculou: fontes demo não têm PUUID real
    algum (hash não-secreto derivado só de match_id+participant_id, dado
    sintético); a fonte Riot usa HMAC-SHA256 com segredo local separado
    (RIFTLAB_SUBJECT_HASH_SECRET), nunca hash simples nem chave fixa."""

    participant_id: int
    champion_id: int
    team: int
    subject_hash: str
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
