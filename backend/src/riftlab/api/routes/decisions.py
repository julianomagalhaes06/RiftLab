"""GET /api/v1/matches/{matchId}/decisions e GET /api/v1/decisions/{decisionId}
(CC-204, fronteira D2). Só leitura de estados JÁ extraídos: nunca reconstrói,
extrai, abre fonte, acessa rede nem grava (transação READ ONLY). Só informação
pré-decisão (modo Estudo: antes de revelar, nada da compra histórica): ver
decisions/queries.py."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from riftlab.decisions.domain import StoredDecisionPoint
from riftlab.decisions.queries import (
    DecisionDetail,
    get_decision_detail,
    list_decision_points,
    public_reasons,
)
from riftlab.errors import ProblemDetail
from riftlab.shared.api.common import (
    MATCH_ID_DEMO_PATTERN,
    PAGINATION_DEFAULT_LIMIT,
    PAGINATION_MAX_LIMIT,
    CamelModel,
    CursorPage,
    MatchId,
)
from riftlab.shared.api.enums import BudgetSource
from riftlab.shared.api.matches import (
    DecisionBudget,
    DecisionInventory,
    DecisionReason,
    DecisionState,
    DecisionStateSummary,
    DecisionVersionInfo,
    InventoryItem,
    ObservedLevel,
)
from riftlab.shared.errors import NotFoundError

router = APIRouter(tags=["decisions"])


class TraceEvent(CamelModel):
    """Evento da linha do tempo da Riot. `raw` traz os campos como vieram da API (tipo,
    momento em ms e os do próprio jogador: participante, item, nível, ouro devolvido).
    `hidden`: campos retirados para não revelar a compra no modo Estudo (só no evento da
    própria decisão)."""

    source_index: int
    timestamp_ms: int
    type: str
    item_id: int | None = None
    item_name: str | None = None
    level: int | None = None
    hidden: list[str] = []
    raw: dict[str, Any]


class TraceFrame(CamelModel):
    """Quadro da linha do tempo (um por minuto) anterior à compra, só deste jogador."""

    timestamp_ms: int
    gold: int | None = None
    level: int | None = None
    cs: int | None = None
    xp: int | None = None


class DecisionTrace(CamelModel):
    """Caminho do dado oficial da Riot até a decisão. `timelineEvents`: eventos da partida;
    `participantItemEvents`: eventos de item do jogador do início até a compra (refazem o
    inventário); `event`: o evento que originou a decisão, sem o item comprado; `nearby`:
    eventos de item e de nível do jogador nos 2 min antes do instante da compra (no máximo 24);
    `frame`: o último quadro antes da compra. Nada revela a compra (modo Estudo)."""

    decision_id: uuid.UUID
    match_id: str
    participant_id: int
    patch: str
    queue: int
    timeline_events: int
    participant_item_events: int
    event: TraceEvent | None = None
    nearby: list[TraceEvent]
    frame: TraceFrame | None = None
    frame_count: int


_NOT_FOUND = {
    "model": ProblemDetail,
    "description": "Partida, participante ou decisão inexistente.",
}
_NOT_PREPARED = {
    "model": ProblemDetail,
    "description": (
        "Código DECISIONS_NOT_PREPARED: a partida existe, mas as decisões da versão "
        "corrente ainda não foram extraídas (cc timeline reconstruct + cc decisions extract). "
        "Distinto de 200 com items=[] (extraída com zero pontos)."
    ),
}
_INVALID = {
    "model": ProblemDetail,
    "description": (
        "VALIDATION_ERROR (parâmetro inválido) ou INVALID_CURSOR (cursor malformado, "
        "adulterado, de outra partida/participante ou de extração inexistente)."
    ),
}


@router.get(
    "/api/v1/matches/{match_id}/decisions",
    response_model=CursorPage[DecisionStateSummary],
    responses={404: _NOT_FOUND, 409: _NOT_PREPARED, 422: _INVALID},
)
async def list_match_decisions(
    match_id: MatchId,
    request: Request,
    participant_id: Annotated[int, Query(alias="participantId", ge=1, le=10)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=PAGINATION_MAX_LIMIT)] = PAGINATION_DEFAULT_LIMIT,
) -> CursorPage[DecisionStateSummary]:
    settings = request.app.state.settings
    page = await list_decision_points(
        request.app.state.session_factory,
        match_id,
        participant_id,
        limit=limit,
        cursor=cursor,
        scope_path=Path(settings.scope_path),
    )
    return CursorPage[DecisionStateSummary](
        items=[
            _to_summary(point).model_copy(
                update={
                    "reconstruction_rules_health": page.health[point.id]["status"],
                    "reconstruction_rules_verdict": page.health[point.id]["verdict"],
                    "reconstruction_rules_wilson_upper": page.health[point.id]["wilsonUpper"],
                }
            )
            for point in page.items
        ],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/api/v1/decisions/{decision_id}",
    response_model=DecisionState,
    responses={
        404: _NOT_FOUND,
        422: {"model": ProblemDetail, "description": "decisionId não é um UUID válido."},
    },
)
async def get_decision(decision_id: uuid.UUID, request: Request) -> DecisionState:
    detail = await get_decision_detail(request.app.state.session_factory, decision_id)
    if not request.app.state.settings.demo_data_enabled and MATCH_ID_DEMO_PATTERN.fullmatch(
        detail.point.match_id
    ):
        raise NotFoundError(f"decisão {decision_id} não encontrada")
    return _to_detail(detail)


@router.get(
    "/api/v1/decisions/{decision_id}/trace",
    response_model=DecisionTrace,
    summary="Da Riot até a análise: os dados oficiais por trás da decisão",
    description="Só leitura no banco; nenhuma chamada à Riot.",
    responses={
        404: _NOT_FOUND,
        422: {"model": ProblemDetail, "description": "decisionId não é um UUID válido."},
    },
)
async def get_decision_trace(decision_id: uuid.UUID, request: Request) -> DecisionTrace:
    from riftlab.decisions.trace import decision_trace

    trace = await decision_trace(request.app.state.session_factory, decision_id)
    if not request.app.state.settings.demo_data_enabled and MATCH_ID_DEMO_PATTERN.fullmatch(
        trace["matchId"]
    ):
        raise NotFoundError(f"decisão {decision_id} não encontrada")
    return DecisionTrace.model_validate(trace)


def _budget_parts(point: StoredDecisionPoint) -> tuple[BudgetSource, int | None, bool]:
    budget = point.budget
    return BudgetSource(budget["origin"]), budget.get("ageMs"), bool(budget["stale"])


def _to_summary(point: StoredDecisionPoint) -> DecisionStateSummary:
    source, age_ms, stale = _budget_parts(point)
    return DecisionStateSummary(
        decision_id=point.id,
        participant_id=point.participant_id,
        event_source_index=point.event_source_index,
        timestamp_ms=point.event_timestamp_ms,
        eligible=point.eligible,
        reason_codes=[reason.code for reason in public_reasons(point.reasons)],
        main_item_ordinal=point.main_item_ordinal,
        budget_source=source,
        frame_age_ms=age_ms,
        stale_frame=stale,
    )


def _to_detail(detail: DecisionDetail) -> DecisionState:
    point = detail.point
    source, age_ms, stale = _budget_parts(point)
    items = detail.inventory.get("items", {})
    versions = point.versions
    return DecisionState(
        decision_id=point.id,
        match_id=point.match_id,
        participant_id=point.participant_id,
        event_source_index=point.event_source_index,
        timestamp_ms=point.event_timestamp_ms,
        eligible=point.eligible,
        main_item_ordinal=point.main_item_ordinal,
        reasons=[
            DecisionReason(code=r.code, sub_reasons=list(r.sub_reasons))
            for r in public_reasons(point.reasons)
        ],
        inventory=DecisionInventory(
            items=[
                InventoryItem(
                    item_id=int(item_id),
                    quantity=quantity,
                    name=detail.item_names.get(int(item_id)),
                )
                for item_id, quantity in sorted(items.items(), key=lambda kv: int(kv[0]))
            ],
            trinket_item_id=detail.inventory.get("trinketItemId"),
        ),
        budget=DecisionBudget(
            value=point.budget.get("value"),
            source=source,
            frame_age_ms=age_ms,
            stale_frame=stale,
            codes=list(point.budget.get("codes", [])),
            immediate_purchase_assertable=bool(point.budget["immediatePurchaseAssertable"]),
        ),
        level=ObservedLevel(
            value=detail.level.get("value"),
            frame_age_ms=detail.level.get("ageMs"),
            absent_reason=detail.level.get("absentReason"),
        ),
        versions=DecisionVersionInfo(
            data_dragon_version=detail.data_dragon_version,
            reconstruction_version=versions.reconstruction_version,
            selection_version=versions.selection_version,
            feature_schema_version=versions.feature_schema_version,
            scope_version=versions.scope_version,
            catalog_id=versions.catalog_id,
            curation_rules_version=versions.curation_rules_version,
            classification_version=versions.classification_version,
            reconstruction_rules=detail.rules_application,
            reconstruction_rules_patch=detail.rules_patch,
            reconstruction_rules_queue=detail.rules_queue,
            reconstruction_rules_health=detail.health.get("status", "UNVERIFIED"),
            reconstruction_rules_verdict=detail.health.get("verdict"),
            reconstruction_rules_wilson_upper=detail.health.get("wilsonUpper"),
            reconstruction_rules_clean_participants=detail.health.get("cleanParticipants", 0),
            reconstruction_rules_failures=detail.health.get("failures", 0),
        ),
    )
