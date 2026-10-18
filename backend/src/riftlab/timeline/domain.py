from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from riftlab.shared.errors import DomainError

CURRENT_DECISION_STATE_SCHEMA_VERSION = "v1"


class TimelineReplayError(DomainError):
    """Evento pertencente ao próprio participante tem payload estruturalmente
    inválido para seu type (ex.: ITEM_PURCHASED sem itemId inteiro) — falha
    permanente da reconstrução (CC-203), nunca uma tentativa de adivinhar o
    campo ausente. Distinto de INVENTORY_UNCERTAIN (domain-model.md): isto é
    corrupção estrutural do dado, não ambiguidade semântica de um dado bem
    formado."""

    def __init__(self, *, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


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
class MatchReconstructionMarker:
    """Prova explícita de que uma partida já foi reconstruída por completo
    (CC-203, revisão): existe independente de qualquer participante ter
    tido compra alguma — checar só `list_decision_states` do primeiro
    participante (tentativa anterior) falha quando esse participante não
    comprou nada, mas outro comprou; a segunda execução então tentava
    reinserir estados já existentes. catalog_id aqui é o valor REALMENTE
    usado quando a reconstrução foi computada — reexecuções idempotentes
    nunca reresolve o canônico atual (que pode ter mudado desde então) e o
    reportam como se fosse o valor usado para os estados já persistidos.

    reconstruction_version identifica QUAL algoritmo produziu os
    `DecisionState`s associados (`state_version` compartilha o mesmo
    número) — nunca inferido do estado do banco. Uma marca de versão
    antiga (ou ausente) nunca é tratada como "já feita para a versão
    atual": a chave primária é (match_id, reconstruction_version), então
    uma partida reconstruída por uma versão anterior do algoritmo (com ou
    sem marca) nunca colide com os `DecisionState`s da versão corrente nem
    é apagada — cada versão tem sua própria linha de dados e sua própria
    marca, histórico completo preservado."""

    match_id: str
    reconstruction_version: int
    catalog_id: uuid.UUID | None
    rules_provenance: list[dict[str, Any]] | None = None


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

    async def list_source_origins(self, match_id: str, state_version: int) -> list[str]:
        """Valores DISTINTOS de `quality.sourceOrigin` dos estados da partida na
        versão dada (sem carregar os estados). Vazio = nenhum estado."""
        ...

    async def get_match_reconstruction(
        self, match_id: str, reconstruction_version: int
    ) -> MatchReconstructionMarker | None:
        """None = partida nunca reconstruída NESSA VERSÃO do algoritmo (ou
        reconstrução anterior não chegou a commitar) — uma marca de versão
        diferente nunca conta como "já feita" (sem isto, um upgrade de
        algoritmo colidiria com `DecisionState`s antigos em vez de tratá-los
        como legado preservado). Existência da marca NESSA versão é a única
        fonte de verdade de idempotência, nunca inferida a partir de
        contagem de estados de um participante específico."""
        ...

    async def add_match_reconstruction(
        self,
        match_id: str,
        reconstruction_version: int,
        catalog_id: uuid.UUID | None,
        rules_provenance: list[dict[str, Any]] | None = None,
    ) -> None:
        """Gravado na MESMA transação dos `DecisionState`s produzidos dessa
        versão — tudo ou nada. Levanta ConflictError (via
        IntegrityError traduzido pelo chamador) se já existe marca para
        este (match_id, reconstruction_version): reconstrução concorrente
        da mesma partida/versão, nunca uma segunda marca silenciosa."""
        ...
