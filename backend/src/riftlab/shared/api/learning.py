"""Contrato de aprendizagem RF13-16 (docs/contracts.md, docs/domain-model.md
"Tentativa de aprendizagem"). Schema-only: nenhuma rota montada nesta etapa
(infraestrutura de tentativa comeca em CC-304)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from riftlab.shared.api.analyses import AnalysisResult
from riftlab.shared.api.common import CamelModel
from riftlab.shared.api.enums import LearningAttemptState
from riftlab.shared.api.objectives import ObjectiveForm

RATIONALE_MAX_LENGTH = 1000


class LearningAttemptCreateRequest(CamelModel):
    """{decisionId, objective} + Idempotency-Key -> 201."""

    decision_id: UUID
    objective: ObjectiveForm
    # CC-505: TRANSFER é a tentativa independente sem pistas (docs/learning-design.md, passo 8).
    mode: Literal["STUDY", "TRANSFER"] = "STUDY"


class LearningAttemptOption(CamelModel):
    """Alternativa exibida antes do reveal: "sem rank/score/evidence que
    revele ordem, sem compra historica; ordem estavel aleatorizada por
    tentativa" (docs/contracts.md) — por isso o schema nao tem esses campos."""

    item_id: int = Field(ge=0)
    name: str | None = None


class LearningAttemptHint(CamelModel):
    """Pista curada (CC-304): fatos disponíveis, nunca um vencedor."""

    level: int = Field(ge=1, le=2)
    text: str = Field(min_length=1, max_length=400)
    source: str = Field(min_length=1, max_length=80)


class LearningAttemptPublicState(CamelModel):
    """Estado publico da tentativa (GET .../{id} e resposta de POST
    .../{id}). context nao tem forma fechada em nenhum doc ainda (o "estado
    imediatamente anterior a decisao" concreto e responsabilidade de
    CC-203/204/304) — mantido como objeto opaco aqui, proposital."""

    attempt_id: UUID
    status: LearningAttemptState
    context: dict[str, Any]
    options: list[LearningAttemptOption]
    hint_level: int = Field(ge=0, le=2)
    analysis_id: UUID | None = None
    decision_id: UUID | None = None
    objective: ObjectiveForm | None = None
    selected_item_id: int | None = None
    skipped: bool = False
    rationale: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    hints: list[LearningAttemptHint] = Field(default_factory=list)
    comparison: AnalysisResult | None = None  # só depois de REVEALED
    historical_item_id: int | None = None  # compra histórica, só depois de REVEALED
    mode: Literal["STUDY", "TRANSFER"] = "STUDY"
    reflection: str | None = None  # CC-505: registrada uma vez, depois da revelação

    @model_validator(mode="after")
    def _analysis_id_only_after_reveal(self) -> LearningAttemptPublicState:
        has_analysis = self.analysis_id is not None
        revealed = self.status == LearningAttemptState.REVEALED
        if (
            has_analysis or self.comparison is not None or self.historical_item_id
        ) and not revealed:
            raise ValueError(
                "analysisId so pode aparecer apos REVEALED (docs/domain-model.md: "
                "analysis_id nullable ate revelacao)"
            )
        return self


class LearningAttemptAnswerRequest(CamelModel):
    """{itemId ou null, rationale?, skipped:boolean}; se not skipped, item
    obrigatorio (docs/contracts.md). rationale maximo 1000 caracteres,
    contratual."""

    item_id: int | None = Field(default=None, ge=0)
    rationale: str | None = Field(default=None, max_length=RATIONALE_MAX_LENGTH)
    skipped: bool = False

    @model_validator(mode="after")
    def _item_required_unless_skipped(self) -> LearningAttemptAnswerRequest:
        if not self.skipped and self.item_id is None:
            raise ValueError("itemId obrigatorio quando skipped=false (docs/contracts.md)")
        return self


class LearningAttemptHintRequest(CamelModel):
    """{level:1|2} — nunca um terceiro nivel; nunca muda ordem nem revela
    ranking (docs/contracts.md)."""

    level: Literal[1, 2]


class LearningAttemptRevealRequest(CamelModel):
    """{explicit:true} — reveal so e valido com confirmacao explicita, nunca
    implicito; Literal[True] rejeita explicit:false em vez de apenas ignorar."""

    explicit: Literal[True]


class LearningAttemptReflectionRequest(CamelModel):
    """CC-505: "o que mudaria se a ameaça ou o orçamento fosse diferente?" — uma vez,
    depois da revelação; até 1000 caracteres."""

    reflection: str = Field(min_length=1, max_length=RATIONALE_MAX_LENGTH)


class LearningProgressCounts(CamelModel):
    attempts: int = Field(ge=0)
    answered: int = Field(ge=0)
    skipped: int = Field(ge=0)
    revealed: int = Field(ge=0)
    used_hints: int = Field(ge=0)
    reflections: int = Field(ge=0)


class LearningProgress(CamelModel):
    """CC-505: progresso local descritivo (contagens). Não é nota, elo nem estimativa de
    habilidade; tentativas não alimentam treino."""

    study: LearningProgressCounts
    transfer: LearningProgressCounts


class LearningAttemptSummary(CamelModel):
    """Uma linha da listagem (biblioteca de decisões, minha evolução): só o que
    identifica e data a tentativa, sem o conteúdo pedagógico (contexto, opções,
    pistas) — quem quiser isso abre `GET /{attemptId}` ou `/decisao/{decisionId}`.
    `reflection` fica de fora de propósito: é texto do usuário, não um resumo."""

    attempt_id: UUID
    decision_id: UUID
    match_id: str
    participant_id: int
    status: LearningAttemptState
    mode: Literal["STUDY", "TRANSFER"]
    skipped: bool
    has_reflection: bool
    analysis_id: UUID | None = None
    created_at: datetime
    revealed_at: datetime | None = None


class LearningGoal(CamelModel):
    """Conquista ou missão: progresso até o alvo (sempre de tentativas reais)."""

    key: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=200)
    progress: int = Field(ge=0)
    target: int = Field(ge=1)
    unlocked: bool | None = None
    completed: bool | None = None


class LearningStreak(CamelModel):
    current: int = Field(ge=0)
    best: int = Field(ge=0)
    studied_today: bool


class LearningMatchProgress(CamelModel):
    """Decisões já estudadas de uma partida e o XP feito nelas."""

    match_id: str
    studied_decision_ids: list[uuid.UUID]
    xp: int = Field(ge=0)


class LearningGamification(CamelModel):
    """Gamificação do estudo: XP e nível, sequência diária (horário de Brasília), conquistas e
    3 missões da semana. Calculado das tentativas reais; não é nota nem estimativa de
    habilidade. XP por decisão (a melhor tentativa): estudar 20, revelar +10, revelar sem dica
    +10, refletir +15."""

    xp: int = Field(ge=0)
    level: int = Field(ge=1)
    level_xp: int = Field(ge=0, description="XP já feito dentro do nível atual.")
    level_target: int = Field(ge=1, description="XP que o nível atual pede para subir.")
    studied_decisions: int = Field(ge=0)
    streak: LearningStreak
    achievements: list[LearningGoal]
    missions: list[LearningGoal]
    week_start: str
    match: LearningMatchProgress | None = None
