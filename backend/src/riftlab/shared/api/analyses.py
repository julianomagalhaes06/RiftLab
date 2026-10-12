"""POST /api/v1/analyses, GET /api/v1/analyses/{id} (docs/contracts.md).
Schema-only: nenhuma rota montada nesta etapa (CC-301-304, CC-401-405)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from riftlab.shared.api.common import MINIMUM_ANALYSIS_REASON_CODES, CamelModel
from riftlab.shared.api.enums import AnalysisEngine, AnalysisStatus, BudgetSource
from riftlab.shared.api.objectives import ObjectiveForm

BUDGET_GOLD_MIN = 0
BUDGET_GOLD_MAX = 100_000


class UserProvidedBudget(CamelModel):
    """docs/contracts.md: Budget opcional {gold: inteiro 0..100000, source:
    USER_PROVIDED}. source e Literal fixo: o cliente nunca pode declarar
    FRAME_ESTIMATE/UNKNOWN, que so o servidor atribui — "fonte de orcamento do
    servidor nao e sobrescrita, cria cenario separado"."""

    gold: int = Field(ge=BUDGET_GOLD_MIN, le=BUDGET_GOLD_MAX)
    source: Literal[BudgetSource.USER_PROVIDED] = BudgetSource.USER_PROVIDED


class AnalysisRequest(CamelModel):
    """{decisionId,objective,budget?} + Idempotency-Key (header HTTP, fora do
    corpo). Reenvio da mesma chave com corpo identico e idempotente (200);
    corpo divergente e 409 IDEMPOTENCY_CONFLICT — validado no handler, nao
    neste schema de corpo."""

    decision_id: UUID
    objective: ObjectiveForm
    budget: UserProvidedBudget | None = None


class AnalysisVersions(CamelModel):
    model: UUID | None = None  # None quando nao ha modelo ativo (baseline explicito)
    catalog: str = Field(min_length=1, max_length=40)
    features: str = Field(min_length=1, max_length=20)
    candidates: str = Field(min_length=1, max_length=20)
    ranking: str | None = Field(default=None, max_length=40)  # CC-303
    objective: str | None = Field(default=None, max_length=40)  # CC-304
    item_rules: str | None = Field(default=None, max_length=60)
    registry_version: UUID | None = None  # CC-405: versão ativa do registro (baseline ou modelo)


class AnalysisQuality(CamelModel):
    """frameAgeMs e None quando nao ha snapshot anterior para estimar (ex.:
    budgetSource=UNKNOWN) — ausencia real e mascara null, nunca 0 inventado
    (docs/domain-model.md, mesmo padrao de shared.api.matches.DecisionState).
    Idade negativa continua rejeitada quando o valor esta presente."""

    budget_source: BudgetSource
    frame_age_ms: int | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list)
    # CC-505: ouro informado no cenário manual (budgetSource=USER_PROVIDED); nulo fora dele.
    user_gold: int | None = Field(default=None, ge=0, le=100000)


class AnalysisSupport(CamelModel):
    """level nao e enumerado por completo em docs/contracts.md (so o exemplo
    "LIMITED"); reasonCodes e lista aberta ("minimos", ver
    common.MINIMUM_ANALYSIS_REASON_CODES)."""

    level: str = Field(min_length=1, max_length=40)
    reason_codes: list[str] = Field(default_factory=list)
    legal_candidates: int | None = Field(default=None, ge=0)
    reference_decisions: int | None = Field(default=None, ge=0)


class AnalysisExplanation(CamelModel):
    """CC-305: frase determinística e o campo real/versão de onde vem."""

    text: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, max_length=80)


class AnalysisCandidate(CamelModel):
    item_id: int = Field(ge=0)
    name: str | None = None
    rank: int = Field(ge=1)
    base_rank: int | None = Field(default=None, ge=1)  # ordem antes do objetivo (CC-304)
    score: float
    remaining_cost: int = Field(ge=0)
    affordability: str = Field(min_length=1, max_length=20)
    evidence_codes: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)  # atributos do catálogo (públicos)
    explanations: list[AnalysisExplanation] = Field(default_factory=list)


class AnalysisResult(CamelModel):
    """GET /analyses/{id}: resultado imutavel. Invariante de
    docs/contracts.md: "Sem evidencia: candidates=[] e reasonCodes
    obrigatorio" — status != AVAILABLE implica candidates vazio e ao menos um
    reason code."""

    analysis_id: UUID
    decision_id: UUID
    status: AnalysisStatus
    engine: AnalysisEngine
    objective: ObjectiveForm
    versions: AnalysisVersions
    quality: AnalysisQuality
    support: AnalysisSupport
    candidates: list[AnalysisCandidate] = Field(default_factory=list)
    score_note: str | None = None

    @model_validator(mode="after")
    def _no_candidates_without_evidence(self) -> AnalysisResult:
        if self.status != AnalysisStatus.AVAILABLE:
            if self.candidates:
                raise ValueError(
                    "candidates deve ser vazio quando status != AVAILABLE (docs/contracts.md)"
                )
            if not self.support.reason_codes:
                raise ValueError(
                    "reasonCodes e obrigatorio quando nao ha evidencia (docs/contracts.md)"
                )
        return self


__all__ = [
    "MINIMUM_ANALYSIS_REASON_CODES",
    "AnalysisCandidate",
    "AnalysisExplanation",
    "AnalysisQuality",
    "AnalysisRequest",
    "AnalysisResult",
    "AnalysisSupport",
    "AnalysisVersions",
    "UserProvidedBudget",
]
