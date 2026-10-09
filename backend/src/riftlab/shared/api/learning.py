"""Contrato de aprendizagem RF13-16 (docs/contracts.md, docs/domain-model.md
"Tentativa de aprendizagem"). Schema-only: nenhuma rota montada nesta etapa
(infraestrutura de tentativa comeca em CC-304)."""

from __future__ import annotations

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
