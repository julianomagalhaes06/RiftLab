"""Features v1 (CC-204): extração pré-decisão a partir de uma VISÃO restrita
do estado (`PreDecisionView`), nunca do estado inteiro. A visão só carrega o
que é conhecido no instante da decisão; ela NÃO tem campo para o item
comprado, para o bloco retrospectivo, para a qualidade cumulativa
(`quality.uncertain/reasons`), para hash pessoal nem para qualquer resultado.
Por isso a saída depende só do prefixo do que já era conhecido — perturbar o
futuro, o alvo ou campos finais não pode alterá-la.

Contrato de schema: `FEATURE_SPECS` (código) e `configs/feature-schema-v1.json`
descrevem os mesmos campos e são testados um contra o outro. Por campo: tipo,
nulidade, proveniência, limite de observabilidade e máscara. Null nunca vira
zero: ausência é sempre acompanhada de uma máscara explícita."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from riftlab.decisions.domain import (
    FEATURE_SCHEMA_VERSION,
    IncompatibleStateError,
    MatchContext,
    ParticipantView,
)
from riftlab.shared.api.enums import BudgetSource
from riftlab.shared.numeric import is_ascii_digits
from riftlab.timeline.application import (
    RECONSTRUCTION_ALGORITHM_VERSION,
    STALE_FRAME_THRESHOLD_MS,
)
from riftlab.timeline.domain import DecisionState

MASK_PRESENT = "PRESENT"
MASK_NO_PRIOR_FRAME = "NO_PRIOR_FRAME"
MASK_FIELD_ABSENT = "FIELD_ABSENT"
MASK_UNAVAILABLE_IN_SCHEMA = "UNAVAILABLE_IN_SCHEMA"


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    dtype: str
    nullable: bool
    provenance: str
    observability: str
    masks: tuple[str, ...]


_FRAME_MASKS = (MASK_PRESENT, MASK_NO_PRIOR_FRAME, MASK_FIELD_ABSENT)
_AGE_MASKS = (MASK_PRESENT, MASK_NO_PRIOR_FRAME)

FEATURE_SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        "self_champion_id",
        "int",
        False,
        "participants.champion_id",
        "CHAMPION_SELECT_PUBLIC",
        (MASK_PRESENT,),
    ),
    FeatureSpec(
        "self_role",
        "str",
        True,
        "participants.role",
        "CHAMPION_SELECT_PUBLIC",
        (MASK_PRESENT, MASK_FIELD_ABSENT),
    ),
    FeatureSpec(
        "self_team", "int", False, "participants.team", "CHAMPION_SELECT_PUBLIC", (MASK_PRESENT,)
    ),
    FeatureSpec(
        "ally_champion_ids",
        "list[int]",
        False,
        "participants.champion_id (mesmo time, exclui o próprio, ordem crescente)",
        "CHAMPION_SELECT_PUBLIC",
        (MASK_PRESENT,),
    ),
    FeatureSpec(
        "enemy_champion_ids",
        "list[int]",
        False,
        "participants.champion_id (time adversário, ordem crescente)",
        "CHAMPION_SELECT_PUBLIC",
        (MASK_PRESENT,),
    ),
    FeatureSpec(
        "ally_composition_aggregates",
        "null",
        True,
        "indisponível: não existe catálogo curado de campeões",
        "UNAVAILABLE",
        (MASK_UNAVAILABLE_IN_SCHEMA,),
    ),
    FeatureSpec(
        "enemy_composition_aggregates",
        "null",
        True,
        "indisponível: não existe catálogo curado de campeões",
        "UNAVAILABLE",
        (MASK_UNAVAILABLE_IN_SCHEMA,),
    ),
    FeatureSpec(
        "game_time_ms",
        "int",
        False,
        "snapshot.eventTimestampMs (instante da decisão)",
        "OWN_STATE_EVENT_REPLAY",
        (MASK_PRESENT,),
    ),
    FeatureSpec(
        "level",
        "int",
        True,
        "quality.levelObservation.value (último frame estritamente anterior)",
        "OWN_STATE_FRAME_OBSERVATION",
        _FRAME_MASKS,
    ),
    FeatureSpec(
        "level_age_ms",
        "int",
        True,
        "quality.levelObservation.ageMs (idade do frame)",
        "OWN_STATE_FRAME_OBSERVATION",
        _AGE_MASKS,
    ),
    FeatureSpec(
        "gold_estimate",
        "int",
        True,
        "quality.goldEstimate.value (último frame estritamente anterior)",
        "OWN_STATE_FRAME_OBSERVATION",
        _FRAME_MASKS,
    ),
    FeatureSpec(
        "gold_origin",
        "str",
        False,
        "quality.goldEstimate.origin (vocabulário budget_source)",
        "OWN_STATE_FRAME_OBSERVATION",
        (MASK_PRESENT,),
    ),
    FeatureSpec(
        "gold_age_ms",
        "int",
        True,
        "quality.goldEstimate.ageMs (idade do frame)",
        "OWN_STATE_FRAME_OBSERVATION",
        _AGE_MASKS,
    ),
    FeatureSpec(
        "gold_stale",
        "bool",
        True,
        "derivado de quality.goldEstimate.ageMs > 90000",
        "OWN_STATE_FRAME_OBSERVATION",
        (MASK_PRESENT, MASK_NO_PRIOR_FRAME),
    ),
    FeatureSpec(
        "inventory_items",
        "list[list[int]]",
        False,
        "snapshot.inventory.items (multiconjunto antes da compra; pares [item_id, quantidade])",
        "OWN_STATE_EVENT_REPLAY",
        (MASK_PRESENT,),
    ),
    FeatureSpec(
        "inventory_trinket_item_id",
        "int",
        True,
        "snapshot.inventory.trinketItemId (null = slot vazio)",
        "OWN_STATE_EVENT_REPLAY",
        (MASK_PRESENT,),
    ),
    FeatureSpec(
        "main_item_ordinal",
        "int",
        False,
        "unidades de item principal em snapshot.inventory.items + item_classification",
        "OWN_STATE_EVENT_REPLAY",
        (MASK_PRESENT,),
    ),
    FeatureSpec("patch", "str", False, "match.patch", "MATCH_METADATA", (MASK_PRESENT,)),
)

FEATURE_ALLOWLIST: tuple[str, ...] = tuple(spec.name for spec in FEATURE_SPECS)

# Nomes proibidos em qualquer feature, visão ou projeção de participante.
FORBIDDEN_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "subject_hash",
        "puuid",
        "riot_id",
        "summoner_name",
        "purchased_item_id",
        "purchasedItemId",
        "target_item_id",
        "retrospective_target",
        "retrospectiveTarget",
        "uncertain",
        "reasons",
        "quality_reasons",
        "win",
        "outcome",
        "result",
        "duration_seconds",
        "duration",
        "final_inventory",
        "final_items",
        "kills",
        "deaths",
        "assists",
        "damage",
        "final_gold",
        "total_gold",
    }
)


@dataclass(frozen=True, slots=True)
class GoldView:
    value: int | None
    origin: str
    age_ms: int | None


@dataclass(frozen=True, slots=True)
class LevelView:
    value: int | None
    age_ms: int | None
    absent_reason: str | None


@dataclass(frozen=True, slots=True)
class PreReason:
    code: str
    item_id: int | None


@dataclass(frozen=True, slots=True)
class PreDecisionView:
    """Tudo que uma feature pode ler. Sem item comprado, sem bloco
    retrospectivo, sem qualidade cumulativa, sem hash pessoal."""

    state_id: uuid.UUID
    match_id: str
    participant_id: int
    event_source_index: int
    event_timestamp_ms: int
    inventory_items: tuple[tuple[int, int], ...]
    trinket_item_id: int | None
    gold: GoldView
    level: LevelView
    pre_decision_uncertain: bool
    pre_decision_reasons: tuple[PreReason, ...]


def _malformed(detail: str) -> IncompatibleStateError:
    return IncompatibleStateError(code="STATE_MALFORMED", detail=detail)


def _int_or_none(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _malformed(f"{field} não é inteiro")
    return value


def _mapping(container: Any, key: str) -> dict[str, Any]:
    value = container.get(key) if isinstance(container, dict) else None
    if not isinstance(value, dict):
        raise _malformed(f"bloco {key} ausente ou inválido")
    return value


def require_current_version(state: DecisionState) -> None:
    """Estado de outra versão é rejeitado com erro explícito, sem fallback."""
    if state.state_version != RECONSTRUCTION_ALGORITHM_VERSION:
        raise IncompatibleStateError(
            code="STATE_VERSION_INCOMPATIBLE",
            detail=(
                f"estado v{state.state_version}; este módulo consome somente "
                f"v{RECONSTRUCTION_ALGORITHM_VERSION} e não reinterpreta versões antigas"
            ),
        )


def build_pre_decision_view(state: DecisionState) -> PreDecisionView:
    require_current_version(state)
    inventory = _mapping(state.snapshot, "inventory")
    raw_items = _mapping(inventory, "items")
    items: list[tuple[int, int]] = []
    for raw_id, raw_count in raw_items.items():
        if not is_ascii_digits(raw_id):
            raise _malformed("chave de inventory.items inválida")
        if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 1:
            raise _malformed("quantidade de inventory.items inválida")
        items.append((int(raw_id), raw_count))
    event_ts = _int_or_none(state.snapshot.get("eventTimestampMs"), "snapshot.eventTimestampMs")
    if event_ts is None or event_ts < 0:
        raise _malformed("snapshot.eventTimestampMs ausente")

    gold_payload = _mapping(state.quality, "goldEstimate")
    origin = gold_payload.get("origin")
    if origin not in (BudgetSource.FRAME_ESTIMATE.value, BudgetSource.UNKNOWN.value):
        raise _malformed("goldEstimate.origin inválido")
    gold = GoldView(
        _int_or_none(gold_payload.get("value"), "goldEstimate.value"),
        origin,
        _int_or_none(gold_payload.get("ageMs"), "goldEstimate.ageMs"),
    )
    level_payload = _mapping(state.quality, "levelObservation")
    absent = level_payload.get("absentReason")
    if absent is not None and not isinstance(absent, str):
        raise _malformed("levelObservation.absentReason inválido")
    level = LevelView(
        _int_or_none(level_payload.get("value"), "levelObservation.value"),
        _int_or_none(level_payload.get("ageMs"), "levelObservation.ageMs"),
        absent,
    )

    pre = _mapping(state.quality, "preDecisionQuality")
    pre_uncertain = pre.get("uncertain")
    raw_reasons = pre.get("reasons")
    if not isinstance(pre_uncertain, bool) or not isinstance(raw_reasons, list):
        raise _malformed("preDecisionQuality inválido")
    reasons: list[PreReason] = []
    for raw in raw_reasons:
        code = raw.get("code") if isinstance(raw, dict) else None
        if not isinstance(code, str) or not code:
            raise _malformed("preDecisionQuality.reasons com código inválido")
        reasons.append(PreReason(code, _int_or_none(raw.get("itemId"), "reason.itemId")))

    trinket = _int_or_none(inventory.get("trinketItemId"), "inventory.trinketItemId")
    return PreDecisionView(
        state_id=state.id,
        match_id=state.match_id,
        participant_id=state.participant_id,
        event_source_index=state.event_source_index,
        event_timestamp_ms=event_ts,
        inventory_items=tuple(sorted(items)),
        trinket_item_id=trinket,
        gold=gold,
        level=level,
        pre_decision_uncertain=pre_uncertain,
        pre_decision_reasons=tuple(reasons),
    )


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """Valores + máscaras na ordem do schema. `provenance` vem do schema."""

    schema_version: str
    values: dict[str, Any]
    masks: dict[str, str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "featureSchemaVersion": self.schema_version,
            "values": dict(self.values),
            "masks": dict(self.masks),
            "provenance": {spec.name: spec.provenance for spec in FEATURE_SPECS},
        }


def _frame_mask(value: Any, age_ms: int | None) -> str:
    if value is not None:
        return MASK_PRESENT
    return MASK_NO_PRIOR_FRAME if age_ms is None else MASK_FIELD_ABSENT


def _age_mask(age_ms: int | None) -> str:
    return MASK_PRESENT if age_ms is not None else MASK_NO_PRIOR_FRAME


def extract_features(
    view: PreDecisionView,
    *,
    participants: tuple[ParticipantView, ...],
    match: MatchContext,
    main_item_ordinal: int,
) -> FeatureVector:
    own = next((p for p in participants if p.participant_id == view.participant_id), None)
    if own is None:
        raise IncompatibleStateError(
            code="PARTICIPANT_NOT_IN_MATCH", detail="participante da decisão ausente da partida"
        )
    allies = sorted(
        p.champion_id
        for p in participants
        if p.team == own.team and p.participant_id != own.participant_id
    )
    enemies = sorted(p.champion_id for p in participants if p.team != own.team)

    values: dict[str, Any] = {}
    masks: dict[str, str] = {}

    def put(name: str, value: Any, mask: str = MASK_PRESENT) -> None:
        values[name] = value
        masks[name] = mask

    put("self_champion_id", own.champion_id)
    put("self_role", own.role, MASK_PRESENT if own.role is not None else MASK_FIELD_ABSENT)
    put("self_team", own.team)
    put("ally_champion_ids", allies)
    put("enemy_champion_ids", enemies)
    put("ally_composition_aggregates", None, MASK_UNAVAILABLE_IN_SCHEMA)
    put("enemy_composition_aggregates", None, MASK_UNAVAILABLE_IN_SCHEMA)
    put("game_time_ms", view.event_timestamp_ms)

    level_mask = (
        MASK_PRESENT
        if view.level.value is not None
        else (
            MASK_NO_PRIOR_FRAME
            if view.level.absent_reason == "NO_PRIOR_FRAME"
            else MASK_FIELD_ABSENT
        )
    )
    put("level", view.level.value, level_mask)
    put("level_age_ms", view.level.age_ms, _age_mask(view.level.age_ms))

    gold_mask = _frame_mask(view.gold.value, view.gold.age_ms)
    put("gold_estimate", view.gold.value, gold_mask)
    put("gold_origin", view.gold.origin)
    put("gold_age_ms", view.gold.age_ms, _age_mask(view.gold.age_ms))
    if view.gold.age_ms is None:
        put("gold_stale", None, MASK_NO_PRIOR_FRAME)
    else:
        put("gold_stale", view.gold.age_ms > STALE_FRAME_THRESHOLD_MS)

    put("inventory_items", [[item_id, count] for item_id, count in view.inventory_items])
    put("inventory_trinket_item_id", view.trinket_item_id)
    put("main_item_ordinal", main_item_ordinal)
    put("patch", match.patch)

    assert tuple(values) == FEATURE_ALLOWLIST  # schema e extração nunca divergem
    return FeatureVector(FEATURE_SCHEMA_VERSION, values, masks)
