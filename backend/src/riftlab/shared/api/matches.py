"""GET /api/v1/matches, GET /api/v1/matches/{id}, GET .../decisions,
GET /api/v1/decisions/{id} (docs/contracts.md). Schema-only: nenhuma rota
montada nesta etapa (CC-201/203/204/205)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from riftlab.shared.api.common import CamelModel, MatchId
from riftlab.shared.api.enums import BudgetSource

STALE_FRAME_THRESHOLD_MS = 90_000


class MatchSummary(CamelModel):
    """Item de GET /matches: "partidas prontas, sem PUUID" — nenhum campo
    pessoal (subject_hash, Riot ID) aparece aqui por design, so o necessario
    para a lista."""

    match_id: MatchId
    queue: int = Field(ge=0)
    map: int = Field(ge=0)
    patch: str = Field(min_length=1, max_length=20)
    start_at: datetime


class MatchParticipant(CamelModel):
    """docs/domain-model.md: participants tem subject_hash isolado dos dados
    publicos — nunca exposto aqui. A codificacao exata de "team" (ex.: 100/200
    estilo Riot vs 1/2 simplificado) ainda nao foi decidida (CC-201/202); o
    contrato so garante inteiro nao negativo nesta etapa."""

    participant_id: int = Field(ge=1, le=10)
    champion_id: int = Field(ge=1)
    team: int = Field(ge=0)
    role: str = Field(min_length=1, max_length=20)


class MatchDetail(MatchSummary):
    """GET /matches/{id}: "participantes e patch; 404 desconhecido"."""

    participants: list[MatchParticipant]


class DecisionStateSummary(CamelModel):
    """Item de GET /matches/{id}/decisions: "estados resumidos/qualidade"."""

    decision_id: UUID
    participant_id: int = Field(ge=1, le=10)
    event_source_index: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)
    stale_frame: bool


class DecisionState(CamelModel):
    """GET /decisions/{id}: "snapshot, inventario, fonte de orcamento,
    elegibilidade". snapshot/inventory tem forma concreta definida em
    CC-203/204 (reconstrucao de inventario); aqui so o envelope e os campos ja
    fechados pelo contrato (docs/domain-model.md: budget_source, frameAgeMs,
    mais de 90s marca STALE_FRAME). ineligible_reason nao tem nome fechado por
    docs/contracts.md ("elegibilidade" e citado sem forma exata) — proposta
    deste card, a confirmar em CC-204/205."""

    decision_id: UUID
    participant_id: int = Field(ge=1, le=10)
    inventory: list[int] = Field(default_factory=list)
    budget_source: BudgetSource
    frame_age_ms: int | None = Field(default=None, ge=0)
    stale_frame: bool
    eligible: bool
    ineligible_reason: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _stale_frame_consistent_with_age(self) -> DecisionState:
        if self.frame_age_ms is not None and self.frame_age_ms > STALE_FRAME_THRESHOLD_MS:
            if not self.stale_frame:
                raise ValueError(
                    "frameAgeMs > 90000ms deve marcar staleFrame=true (docs/domain-model.md)"
                )
        return self
