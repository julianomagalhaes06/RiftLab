"""GET /api/v1/matches, GET /api/v1/matches/{matchId} e
POST /api/v1/matches/{matchId}/preparation (CC-205, fronteira D3).

As duas leituras só consultam o banco (transação READ ONLY): nunca importam,
preparam, reconstroem, acessam rede nem gravam, e nenhum identificador pessoal
(subject_hash, Riot ID) sai daqui. O POST é o único caminho de escrita: pede
uma NOVA tentativa de preparação local (catálogo -> reconstrução -> extração)
para uma partida já importada; nunca coleta a partida de novo."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping, load_catalog_mapping
from riftlab.champions.application import ChampionAsset as ChampionAssetData
from riftlab.decisions.domain import ParticipantView
from riftlab.errors import ProblemDetail
from riftlab.ingestion.domain import Match
from riftlab.matches.queries import get_match_detail, list_matches
from riftlab.preparation.application import (
    PreparationInfo,
    preparation_info,
    request_preparation,
)
from riftlab.shared.api.common import (
    PAGINATION_DEFAULT_LIMIT,
    PAGINATION_MAX_LIMIT,
    CursorPage,
    MatchId,
)
from riftlab.shared.api.enums import PreparationStatus
from riftlab.shared.api.matches import (
    ChampionAsset,
    MatchChampion,
    MatchDetail,
    MatchParticipant,
    MatchPreparation,
    MatchSummary,
)
from riftlab.shared.errors import SourceSchemaError

router = APIRouter(tags=["matches"])

_NOT_FOUND = {
    "model": ProblemDetail,
    "description": "Partida inexistente (ou sem importação concluída, no POST).",
}
_INVALID = {
    "model": ProblemDetail,
    "description": (
        "VALIDATION_ERROR (parâmetro inválido) ou INVALID_CURSOR (cursor malformado, "
        "adulterado ou de versão desconhecida)."
    ),
}


@router.get(
    "/api/v1/matches",
    response_model=CursorPage[MatchSummary],
    summary="Lista partidas importadas",
    description=(
        "Somente partidas já importadas. Ordem estável e documentada: `startAt` "
        "decrescente, desempate por `matchId` decrescente; paginação por cursor opaco "
        "sobre essa dupla. Nenhum identificador pessoal é exposto."
    ),
    responses={422: _INVALID},
)
async def list_imported_matches(
    request: Request,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=PAGINATION_MAX_LIMIT)] = PAGINATION_DEFAULT_LIMIT,
) -> CursorPage[MatchSummary]:
    page = await list_matches(
        request.app.state.session_factory,
        limit=limit,
        cursor=cursor,
        mapping=_catalog_mapping_or_none(Path(request.app.state.settings.catalog_mappings_path)),
    )
    return CursorPage[MatchSummary](
        items=[
            _to_summary(item.match, item.preparation, item.champions_present) for item in page.items
        ],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/api/v1/matches/{match_id}",
    response_model=MatchDetail,
    summary="Detalhe de uma partida importada",
    responses={404: _NOT_FOUND, 422: _INVALID},
)
async def get_imported_match(match_id: MatchId, request: Request) -> MatchDetail:
    detail = await get_match_detail(
        request.app.state.session_factory,
        match_id,
        _catalog_mapping_or_none(Path(request.app.state.settings.catalog_mappings_path)),
    )
    summary = _to_summary(
        detail.match,
        detail.preparation,
        tuple(
            (
                p.champion_id,
                detail.champions[p.champion_id].name if p.champion_id in detail.champions else None,
            )
            for p in detail.participants
        ),
    )
    return MatchDetail(
        **summary.model_dump(),
        participants=[_to_participant(p, detail.champions) for p in detail.participants],
        preparation=_to_preparation(detail.preparation),
    )


@router.post(
    "/api/v1/matches/{match_id}/preparation",
    response_model=MatchPreparation,
    status_code=202,
    summary="Pede nova tentativa de preparação da partida",
    description=(
        "Caminho mínimo de nova tentativa quando a preparação terminou em FAILED. "
        "Idempotente: se já há preparação ativa (PENDING/RUNNING) devolve-a; se está "
        "READY com decisões das versões correntes, devolve-a (200); caso contrário "
        "enfileira uma nova tentativa (202), preservando o histórico. Não coleta a "
        "partida de novo e não chama a Riot."
    ),
    responses={404: _NOT_FOUND, 422: _INVALID},
)
async def prepare_match(
    match_id: MatchId, request: Request, response: Response
) -> MatchPreparation:
    settings = request.app.state.settings
    job = await request_preparation(
        request.app.state.session_factory, match_id, scope_path=Path(settings.scope_path)
    )
    info = preparation_info(job)
    if info.status is PreparationStatus.READY:
        response.status_code = 200
    return _to_preparation(info)


def _to_summary(
    match: Match,
    preparation: PreparationInfo,
    champions_present: tuple[tuple[int, str | None], ...],
) -> MatchSummary:
    return MatchSummary(
        match_id=match.match_id,
        queue=match.queue,
        map=match.map_id,
        patch=match.patch,
        start_at=match.start_at,
        duration_seconds=match.duration_seconds,
        preparation_status=preparation.status,
        champions_present=[
            MatchChampion(champion_id=champion_id, name=name)
            for champion_id, name in champions_present
        ],
    )


def _catalog_mapping_or_none(path: Path) -> CatalogMapping | None:
    """Mapping explícito lido do arquivo local (sem rede). Arquivo ausente ou inválido:
    a partida continua legível, só sem nomes/retratos (CC-208)."""
    try:
        return load_catalog_mapping(path) if path.is_file() else None
    except SourceSchemaError:
        return None


def _to_participant(
    view: ParticipantView, champions: Mapping[int, ChampionAssetData]
) -> MatchParticipant:
    asset = champions.get(view.champion_id)
    return MatchParticipant(
        participant_id=view.participant_id,
        champion_id=view.champion_id,
        team=view.team,
        role=view.role,
        champion=(
            ChampionAsset(
                name=asset.name,
                data_dragon_version=asset.data_dragon_version,
                image_file=asset.image_file,
            )
            if asset is not None
            else None
        ),
    )


def _to_preparation(info: PreparationInfo) -> MatchPreparation:
    return MatchPreparation(
        status=info.status,
        error_code=info.error_code,
        retry_at=info.retry_at,
        attempts=info.attempts,
    )
