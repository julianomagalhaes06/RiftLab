"""Seleção retrospectiva de pontos de decisão, elegibilidade e orçamento
(CC-204, `decision_selection_v1`). Módulo puro.

Duas etapas com dados DIFERENTES, propositalmente separadas:
- Seleção RETROSPECTIVA (usa o item comprado e o bloco retrospectivo): decide
  se a compra vira um ponto de decisão listado e, se listada, por que é
  inelegível quando o alvo não é classificável, foi revertido por undo ou tem
  status incerto.
- Elegibilidade de CONTEXTO e features (usam só `PreDecisionView`): qualidade
  pré-decisão, catálogo, recorte e ordinal do item principal. Nunca a
  qualidade cumulativa (`quality.uncertain/reasons`), nunca o alvo.

O orçamento imediato é avaliado à parte: STALE_FRAME/UNKNOWN_BUDGET impedem
afirmar compra imediata, mas não tornam a decisão inelegível."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from riftlab.catalog.classification import (
    ITEM_CLASSIFICATION_VERSION,
    ItemClass,
    ItemClassificationTable,
)
from riftlab.catalog.domain import CatalogVersion
from riftlab.decisions.domain import (
    BUDGET_STALE_FRAME,
    BUDGET_UNKNOWN_BUDGET,
    DECISION_SELECTION_VERSION,
    FEATURE_SCHEMA_VERSION,
    REASON_INVENTORY_UNCERTAIN,
    REASON_MISSING_CATALOG,
    REASON_OUT_OF_SCOPE,
    REASON_TARGET_NOT_CLASSIFIABLE,
    REASON_TARGET_REVERTED,
    REASON_TARGET_STATUS_UNCERTAIN,
    BudgetAssessment,
    DecisionInputError,
    DecisionVersions,
    EligibilityReason,
    IncompatibleStateError,
    MatchContext,
    ParticipantView,
)
from riftlab.decisions.features import (
    FeatureVector,
    PreDecisionView,
    build_pre_decision_view,
    extract_features,
    require_current_version,
)
from riftlab.decisions.scope import DecisionScope
from riftlab.shared.api.enums import BudgetSource
from riftlab.timeline.application import (
    RECONSTRUCTION_ALGORITHM_VERSION,
    RETROSPECTIVE_COMPLETED,
    RETROSPECTIVE_REVERTED_BY_UNDO,
    RETROSPECTIVE_STATUS_UNCERTAIN,
    STALE_FRAME_THRESHOLD_MS,
)
from riftlab.timeline.domain import DecisionState

_UNCLASSIFIABLE = frozenset({ItemClass.UNKNOWN, ItemClass.EXCLUDED, ItemClass.UNCLASSIFIED})
_NO_CATALOG_CODE = "NO_CATALOG"


@dataclass(frozen=True, slots=True)
class DecisionPoint:
    """Ponto de decisão listado (elegível ou não). `features` só existe para
    pontos elegíveis. `target_*` são retrospectivos e existem só para
    seleção/diagnóstico: nunca alimentam features."""

    state_id: uuid.UUID
    match_id: str
    participant_id: int
    event_source_index: int
    event_timestamp_ms: int
    target_item_id: int
    target_class: ItemClass | None
    main_item_ordinal: int | None
    eligible: bool
    reasons: tuple[EligibilityReason, ...]
    budget: BudgetAssessment
    features: FeatureVector | None
    versions: DecisionVersions


def _retrospective(state: DecisionState) -> tuple[str, bool]:
    block = state.quality.get("retrospectiveTarget")
    status = block.get("status") if isinstance(block, dict) else None
    if status not in (
        RETROSPECTIVE_COMPLETED,
        RETROSPECTIVE_REVERTED_BY_UNDO,
        RETROSPECTIVE_STATUS_UNCERTAIN,
    ):
        raise IncompatibleStateError(
            code="STATE_MALFORMED", detail="retrospectiveTarget ausente ou com status inválido"
        )
    return status, bool(block.get("correlationUnvalidated")) if isinstance(block, dict) else False


def _target_item_id(state: DecisionState) -> int:
    value = state.snapshot.get("purchasedItemId")
    if isinstance(value, bool) or not isinstance(value, int):
        raise IncompatibleStateError(
            code="STATE_MALFORMED", detail="snapshot.purchasedItemId ausente ou inválido"
        )
    return value


def assess_budget(view: PreDecisionView) -> BudgetAssessment:
    """Orçamento a partir do que foi observado; `stale` é derivado da idade
    (90000 ms não é stale, 90001 ms é), nunca herdado de um sinalizador."""
    gold = view.gold
    stale = gold.age_ms is not None and gold.age_ms > STALE_FRAME_THRESHOLD_MS
    codes: list[str] = []
    if gold.value is None or gold.origin == BudgetSource.UNKNOWN.value:
        codes.append(BUDGET_UNKNOWN_BUDGET)
    if stale:
        codes.append(BUDGET_STALE_FRAME)
    return BudgetAssessment(
        value=gold.value,
        origin=gold.origin,
        age_ms=gold.age_ms,
        stale=stale,
        codes=tuple(codes),
        immediate_purchase_assertable=not codes,
    )


def _check_catalog_consistency(
    state: DecisionState,
    catalog: CatalogVersion | None,
    classification: ItemClassificationTable | None,
) -> None:
    if state.catalog_id is None:
        if catalog is not None or classification is not None:
            raise DecisionInputError(
                code="CATALOG_MISMATCH",
                detail="estado sem catálogo não aceita catálogo/classificação informados",
            )
        return
    if catalog is None or classification is None:
        raise DecisionInputError(
            code="CATALOG_REQUIRED",
            detail="estado referencia um catálogo: CatalogVersion e classificação são obrigatórios",
        )
    if catalog.id != state.catalog_id:
        raise DecisionInputError(
            code="CATALOG_MISMATCH", detail="catalog_id do estado difere do catálogo informado"
        )
    if classification.curation_rules_version != catalog.curation_rules_version:
        raise DecisionInputError(
            code="CLASSIFICATION_MISMATCH",
            detail="classificação foi calculada para outra versão de curadoria",
        )


def _owned_main_units(
    view: PreDecisionView, classification: ItemClassificationTable
) -> tuple[int, bool]:
    """(unidades de item principal possuídas, algum item possuído inclassificável).
    Trinket fica fora (slot própria); itens não principais conhecidos não contam."""
    main_units = 0
    unclassifiable = False
    for item_id, count in view.inventory_items:
        item_class = classification.classify(item_id).item_class
        if item_class is ItemClass.MAIN:
            main_units += count
        elif item_class in _UNCLASSIFIABLE:
            unclassifiable = True
    return main_units, unclassifiable


def build_versions(scope: DecisionScope, catalog: CatalogVersion | None) -> DecisionVersions:
    """A tupla de versões que identifica uma extração — ÚNICA função, usada pela
    extração e pela leitura (API). `curation_rules_version` vem da LINHA do
    catálogo, nunca da constante do código; a classificação é a versão corrente
    do classificador quando há catálogo (a tabela é derivada do catálogo)."""
    return DecisionVersions(
        reconstruction_version=RECONSTRUCTION_ALGORITHM_VERSION,
        selection_version=DECISION_SELECTION_VERSION,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        scope_version=scope.version,
        catalog_id=catalog.id if catalog else None,
        curation_rules_version=catalog.curation_rules_version if catalog else None,
        classification_version=ITEM_CLASSIFICATION_VERSION if catalog else None,
    )


def _dedupe(codes: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(codes))


def evaluate_decision(
    state: DecisionState,
    *,
    participants: tuple[ParticipantView, ...],
    match: MatchContext,
    catalog: CatalogVersion | None,
    classification: ItemClassificationTable | None,
    scope: DecisionScope,
) -> DecisionPoint | None:
    """None = o alvo é um item conhecido e não principal: não há ponto de
    decisão. Estado de outra versão de reconstrução levanta
    IncompatibleStateError (sem fallback)."""
    require_current_version(state)
    view = build_pre_decision_view(state)
    if match.match_id != state.match_id:
        raise DecisionInputError(code="MATCH_MISMATCH", detail="partida difere da do estado")
    participant = next((p for p in participants if p.participant_id == state.participant_id), None)
    if participant is None:
        raise DecisionInputError(
            code="PARTICIPANT_NOT_IN_MATCH", detail="participante do estado ausente da partida"
        )
    source_origin = state.quality.get("sourceOrigin")
    if source_origin != scope.origin.value:
        raise DecisionInputError(
            code="SCOPE_ORIGIN_MISMATCH",
            detail="recorte informado não corresponde à origem do estado",
        )
    _check_catalog_consistency(state, catalog, classification)

    target_item_id = _target_item_id(state)
    retro_status, retro_unvalidated = _retrospective(state)
    reasons: list[EligibilityReason] = []

    # --- seleção retrospectiva (usa o alvo) ---
    target_class: ItemClass | None = None
    if classification is not None:
        target = classification.classify(target_item_id)
        target_class = target.item_class
        if target_class is not ItemClass.MAIN:
            if target_class not in _UNCLASSIFIABLE:
                return None  # alvo conhecido e não principal: não gera ponto
            reasons.append(
                EligibilityReason(
                    REASON_TARGET_NOT_CLASSIFIABLE, (target_class.value, target.rule_code)
                )
            )

    # Status retrospectivo avaliado SEMPRE, com ou sem catálogo: sem catálogo o
    # alvo não é classificável, mas se a compra foi desfeita (ou tem status
    # incerto) esse motivo precisa ser listado junto de MISSING_CATALOG.
    if retro_status == RETROSPECTIVE_REVERTED_BY_UNDO:
        subs = ["REVERTED_BY_UNDO"]
        if retro_unvalidated:
            subs.append("UNVALIDATED_UNDO_SEMANTICS")
        reasons.append(EligibilityReason(REASON_TARGET_REVERTED, tuple(subs)))
    elif retro_status == RETROSPECTIVE_STATUS_UNCERTAIN:
        reasons.append(
            EligibilityReason(REASON_TARGET_STATUS_UNCERTAIN, (RETROSPECTIVE_STATUS_UNCERTAIN,))
        )

    # --- elegibilidade de contexto (só visão pré-decisão, catálogo, recorte) ---
    pre_codes = [reason.code for reason in view.pre_decision_reasons]
    if classification is None:
        reasons.append(EligibilityReason(REASON_MISSING_CATALOG, (_NO_CATALOG_CODE,)))
    inventory_subs = [code for code in pre_codes if code != _NO_CATALOG_CODE]
    if view.pre_decision_uncertain and not pre_codes:
        inventory_subs.append("UNSPECIFIED_PRE_DECISION_UNCERTAINTY")

    scope_subs = list(scope.out_of_scope_reasons(match, participant))
    ordinal: int | None = None
    if classification is not None:
        main_units, unclassifiable = _owned_main_units(view, classification)
        if unclassifiable:
            inventory_subs.append("OWNED_ITEM_UNCLASSIFIABLE")
        else:
            ordinal = main_units + 1
            if main_units >= 2:
                scope_subs.append("MAIN_ITEM_ORDINAL_BEYOND_SECOND")
    if scope_subs:
        reasons.append(EligibilityReason(REASON_OUT_OF_SCOPE, _dedupe(scope_subs)))
    if inventory_subs:
        reasons.append(EligibilityReason(REASON_INVENTORY_UNCERTAIN, _dedupe(inventory_subs)))

    eligible = not reasons
    features: FeatureVector | None = None
    if eligible:
        assert ordinal is not None  # elegível implica catálogo e ordinal 1 ou 2
        features = extract_features(
            view, participants=participants, match=match, main_item_ordinal=ordinal
        )

    return DecisionPoint(
        state_id=state.id,
        match_id=state.match_id,
        participant_id=state.participant_id,
        event_source_index=state.event_source_index,
        event_timestamp_ms=view.event_timestamp_ms,
        target_item_id=target_item_id,
        target_class=target_class,
        main_item_ordinal=ordinal,
        eligible=eligible,
        reasons=tuple(reasons),
        budget=assess_budget(view),
        features=features,
        versions=build_versions(scope, catalog),
    )


def point_is_actionable(point: DecisionPoint) -> bool:
    """Elegível E orçamento observável e fresco: só então uma recomendação de
    compra imediata pode ser afirmada."""
    return point.eligible and point.budget.immediate_purchase_assertable


__all__ = [
    "DecisionPoint",
    "assess_budget",
    "build_versions",
    "evaluate_decision",
    "point_is_actionable",
]
