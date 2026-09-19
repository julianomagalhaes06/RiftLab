"""Tipos, versões e erros do módulo de decisões (CC-204)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from riftlab.ingestion.domain import Match, Participant
from riftlab.shared.errors import DomainError

DECISION_SELECTION_VERSION = "decision_selection_v1"
FEATURE_SCHEMA_VERSION = "feature_schema_v1"

# Códigos de elegibilidade. Os três primeiros são os mínimos do contrato
# (shared/api/common.py:MINIMUM_ANALYSIS_REASON_CODES) e BLOQUEIAM recomendação;
# os três TARGET_* explicam por que um ponto de decisão listado é inelegível
# pela seleção retrospectiva (o alvo comprado). STALE_FRAME e UNKNOWN_BUDGET
# NÃO são códigos de elegibilidade: pertencem ao orçamento (BudgetAssessment).
REASON_MISSING_CATALOG = "MISSING_CATALOG"
REASON_OUT_OF_SCOPE = "OUT_OF_SCOPE"
REASON_INVENTORY_UNCERTAIN = "INVENTORY_UNCERTAIN"
REASON_TARGET_NOT_CLASSIFIABLE = "TARGET_NOT_CLASSIFIABLE"
REASON_TARGET_REVERTED = "TARGET_REVERTED"
REASON_TARGET_STATUS_UNCERTAIN = "TARGET_STATUS_UNCERTAIN"

BUDGET_STALE_FRAME = "STALE_FRAME"
BUDGET_UNKNOWN_BUDGET = "UNKNOWN_BUDGET"


class DecisionError(DomainError):
    """Erro do módulo de decisões; `code` é estável e seguro para diagnóstico,
    `detail` nunca ecoa payload bruto nem identificador pessoal."""

    def __init__(self, *, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


class IncompatibleStateError(DecisionError):
    """Estado de outra versão de reconstrução (ou malformado): rejeitado sem
    fallback e sem reinterpretação de qualidade antiga."""


class DecisionInputError(DecisionError):
    """Entradas inconsistentes entre si (catálogo, classificação, recorte ou
    participantes que não correspondem ao estado): erro de quem chama, nunca
    silenciado."""


class DecisionConfigError(DecisionError):
    """Documento de recorte inválido."""


@dataclass(frozen=True, slots=True)
class MatchContext:
    """Metadados da partida conhecidos antes de qualquer decisão. Deliberadamente
    sem `duration_seconds`, `start_at` nem `raw_hash`: nada que revele o
    desfecho pode ser carregado por este tipo."""

    match_id: str
    queue: int
    map_id: int
    patch: str

    @classmethod
    def from_match(cls, match: Match) -> MatchContext:
        return cls(match.match_id, match.queue, match.map_id, match.patch)


@dataclass(frozen=True, slots=True)
class ParticipantView:
    """Participante projetado SEM `subject_hash`: o tipo não tem campo para o
    hash, então nada pessoal pode chegar a features ou saídas."""

    participant_id: int
    champion_id: int
    team: int
    role: str | None

    @classmethod
    def from_participant(cls, participant: Participant) -> ParticipantView:
        return cls(
            participant.participant_id,
            participant.champion_id,
            participant.team,
            participant.role,
        )


@dataclass(frozen=True, slots=True)
class EligibilityReason:
    """`code` alinhado ao contrato; `sub_reasons` preserva literalmente os
    códigos originais (ex.: UNVALIDATED_UNDO_SEMANTICS, NO_CATALOG)."""

    code: str
    sub_reasons: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "subReasons": list(self.sub_reasons)}


@dataclass(frozen=True, slots=True)
class BudgetAssessment:
    """Orçamento imediato, separado da elegibilidade de contexto. Preserva
    valor, origem e idade; `null` nunca vira 0. STALE_FRAME e UNKNOWN_BUDGET
    impedem afirmar compra imediata, mas NÃO tornam a decisão inelegível."""

    value: int | None
    origin: str
    age_ms: int | None
    stale: bool
    codes: tuple[str, ...]
    immediate_purchase_assertable: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "origin": self.origin,
            "ageMs": self.age_ms,
            "stale": self.stale,
            "codes": list(self.codes),
            "immediatePurchaseAssertable": self.immediate_purchase_assertable,
        }


@dataclass(frozen=True, slots=True)
class DecisionVersions:
    """Todas as versões que produziram uma saída. `catalog_id`,
    `curation_rules_version` e `classification_version` são None quando não
    há catálogo."""

    reconstruction_version: int
    selection_version: str
    feature_schema_version: str
    scope_version: str
    catalog_id: uuid.UUID | None
    curation_rules_version: str | None
    classification_version: str | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "reconstructionVersion": self.reconstruction_version,
            "selectionVersion": self.selection_version,
            "featureSchemaVersion": self.feature_schema_version,
            "scopeVersion": self.scope_version,
            "catalogId": str(self.catalog_id) if self.catalog_id else None,
            "curationRulesVersion": self.curation_rules_version,
            "classificationVersion": self.classification_version,
        }


@dataclass(frozen=True, slots=True)
class DecisionExtractionMarker:
    """Marca de extração por partida + tupla de versões, gravada na MESMA
    transação dos pontos (tudo ou nada). É a única fonte de idempotência: uma
    marca de outra tupla nunca conta como feita, e `point_count = 0` distingue
    "extraída com zero pontos" de "não extraída" (marca ausente)."""

    id: uuid.UUID
    match_id: str
    versions: DecisionVersions
    point_count: int
    eligible_count: int


@dataclass(frozen=True, slots=True)
class StoredDecisionPoint:
    """Linha imutável de `decision_points`. `target_*` são retrospectivos
    (só diagnóstico/seleção) e nunca entram em `features`; `features` é None
    quando o ponto é inelegível."""

    id: uuid.UUID
    extraction_id: uuid.UUID
    decision_state_id: uuid.UUID
    match_id: str
    participant_id: int
    event_source_index: int
    event_timestamp_ms: int
    versions: DecisionVersions
    target_item_id: int
    target_class: str | None
    main_item_ordinal: int | None
    eligible: bool
    reasons: list[dict[str, Any]]
    budget: dict[str, Any]
    features: dict[str, Any] | None


class DecisionRepository(Protocol):
    async def get_extraction(
        self, match_id: str, versions: DecisionVersions
    ) -> DecisionExtractionMarker | None:
        """None = partida NÃO extraída para ESTA tupla de versões (uma marca
        de outra tupla nunca conta). Colunas de versão nulas comparam por
        igualdade (IS NOT DISTINCT FROM)."""
        ...

    async def add_extraction(self, marker: DecisionExtractionMarker) -> None:
        """Grava a marca (flush imediato, antes dos pontos). Levanta
        ConflictError se já existe marca para (partida, tupla): extração
        concorrente da mesma tupla, nunca uma segunda marca silenciosa."""
        ...

    async def add_point(self, point: StoredDecisionPoint) -> None: ...

    async def get_extraction_by_id(
        self, extraction_id: uuid.UUID
    ) -> DecisionExtractionMarker | None: ...

    async def list_points_page(
        self,
        extraction_id: uuid.UUID,
        participant_id: int,
        after: tuple[int, int, uuid.UUID] | None,
        limit: int,
    ) -> list[StoredDecisionPoint]:
        """Keyset por (event_timestamp_ms, event_source_index, id) dentro de UMA
        extração e UM participante; `after` é a posição do último item já
        entregue (exclusiva). Devolve no máximo `limit` linhas."""
        ...

    async def get_point(self, point_id: uuid.UUID) -> StoredDecisionPoint | None: ...

    async def get_pre_decision_context(
        self, decision_state_id: uuid.UUID
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """(inventário anterior à decisão, observação de nível) do estado de
        origem. Lê SÓ estes dois blocos: o alvo comprado e o bloco retrospectivo
        nunca saem do banco por este caminho."""
        ...

    async def list_points(self, extraction_id: uuid.UUID) -> list[StoredDecisionPoint]:
        """Ordem estável: event_timestamp_ms, event_source_index, id."""
        ...
