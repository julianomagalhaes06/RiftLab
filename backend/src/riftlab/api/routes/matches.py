"""GET /api/v1/matches, GET /api/v1/matches/{matchId} e
POST /api/v1/matches/{matchId}/preparation (CC-205, fronteira D3).

As duas leituras só consultam o banco (transação READ ONLY): nunca importam,
preparam, reconstroem, acessam rede nem gravam, e nenhum identificador pessoal
(subject_hash, Riot ID) sai daqui. A lista marca a posição da conta local
(`localParticipantId`) comparando o hash dentro do banco. O POST é o único caminho de escrita: pede
uma NOVA tentativa de preparação local (catálogo -> reconstrução -> extração)
para uma partida já importada; nunca coleta a partida de novo."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping, load_catalog_mapping
from riftlab.catalog.patches import load_persisted_mapping
from riftlab.champions.application import ChampionAsset as ChampionAssetData
from riftlab.errors import ProblemDetail
from riftlab.ingestion.domain import Match, MatchListScope
from riftlab.ingestion.purge import purge_preview, request_purge
from riftlab.matches.queries import MatchParticipantFactsView, get_match_detail, list_matches
from riftlab.matches.timeline import get_match_timeline
from riftlab.preparation.application import (
    PreparationInfo,
    preparation_info,
    request_preparation,
)
from riftlab.ranked.local import local_subject_hash
from riftlab.shared.api.common import (
    MATCH_ID_DEMO_PATTERN,
    PAGINATION_DEFAULT_LIMIT,
    PAGINATION_MAX_LIMIT,
    CursorPage,
    MatchId,
)
from riftlab.shared.api.enums import ImportJobState, PreparationStatus
from riftlab.shared.api.imports import ImportJobAccepted
from riftlab.shared.api.matches import (
    ChampionAsset,
    MatchChampion,
    MatchDetail,
    MatchParticipant,
    MatchPreparation,
    MatchSummary,
    MatchTimeline,
    ParticipantTimeline,
    TimelineGoldFrame,
    TimelineItemEvent,
    TimelineKill,
    TimelineObjective,
    TimelineSkillUp,
)
from riftlab.shared.api.purge import PurgePreview
from riftlab.shared.errors import SourceSchemaError

router = APIRouter(tags=["matches"])
_DD_VERSION = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}")

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
    scope: Annotated[
        MatchListScope,
        Query(
            description="all: importadas; account: conta local; demo: sintéticas; "
            "account_and_demo: conta local e sintéticas. Filtro anterior à paginação; "
            "conta não identificada nunca inclui partidas reais de terceiros."
        ),
    ] = "all",
) -> CursorPage[MatchSummary]:
    settings = request.app.state.settings
    page = await list_matches(
        request.app.state.session_factory,
        limit=limit,
        cursor=cursor,
        scope=scope,
        mapping=await _catalog_mapping_or_none(
            request.app.state.session_factory,
            Path(settings.catalog_mappings_path),
        ),
        local_subject=lambda session: local_subject_hash(session, settings),
    )
    return CursorPage[MatchSummary](
        items=[
            _to_summary(item.match, item.preparation, item.champions_present).model_copy(
                update={
                    "participants": [_to_participant(p, item.champions) for p in item.participants],
                    "local_participant_id": item.local_participant_id,
                }
            )
            for item in page.items
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
    settings = request.app.state.settings
    detail = await get_match_detail(
        request.app.state.session_factory,
        match_id,
        await _catalog_mapping_or_none(
            request.app.state.session_factory,
            Path(settings.catalog_mappings_path),
        ),
        local_subject=lambda session: local_subject_hash(session, settings),
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
        **summary.model_dump(exclude={"participants", "local_participant_id"}),
        local_participant_id=detail.local_participant_id,
        participants=[_to_participant(p, detail.champions) for p in detail.participants],
        preparation=_to_preparation(detail.preparation),
    )


@router.get(
    "/api/v1/matches/{match_id}/timeline",
    response_model=MatchTimeline,
    summary="Linha do tempo de uma partida importada",
    description=(
        "Compras e vendas por participante (desfazer aplicado), subidas de habilidade, "
        "abates, objetivos e ouro por quadro. Só banco; `goldFrames` é null até a "
        "preparação gravar os quadros."
    ),
    responses={404: _NOT_FOUND, 422: _INVALID},
)
async def get_imported_match_timeline(match_id: MatchId, request: Request) -> MatchTimeline:
    data = await get_match_timeline(request.app.state.session_factory, match_id)
    participant_ids = [p.participant_id for p in data.participants]
    return MatchTimeline(
        match_id=data.match_id,
        # catálogo DEMO usa versão sintética: sem versão do CDN, a interface não monta ícone
        data_dragon_version=(
            data.data_dragon_version
            if data.data_dragon_version and _DD_VERSION.fullmatch(data.data_dragon_version)
            else None
        ),
        participants=[
            ParticipantTimeline(
                participant_id=p.participant_id,
                items=[
                    TimelineItemEvent(
                        timestamp_ms=i.timestamp_ms,
                        item_id=i.item_id,
                        kind=i.kind,  # type: ignore[arg-type]
                        item_class=i.item_class,
                    )
                    for i in p.items
                ],
                skills=[
                    TimelineSkillUp(
                        timestamp_ms=s.timestamp_ms,
                        skill_slot=s.skill_slot,
                        level_up_type=s.level_up_type,
                    )
                    for s in p.skills
                ],
            )
            for p in data.participants
        ],
        kills=[
            TimelineKill(
                timestamp_ms=k.timestamp_ms,
                killer_id=k.killer_id if 0 <= k.killer_id <= 10 else 0,
                victim_id=k.victim_id,
                assisting_ids=[a for a in k.assisting_ids if 1 <= a <= 10],
            )
            for k in data.kills
        ],
        objectives=[
            TimelineObjective(
                timestamp_ms=o.timestamp_ms,
                kind=o.kind,  # type: ignore[arg-type]
                team=o.team,
                sub_type=o.sub_type,
            )
            for o in data.objectives
        ],
        gold_frames=(
            [
                TimelineGoldFrame(
                    timestamp_ms=f.timestamp_ms,
                    gold=[f.gold.get(pid) for pid in range(1, max(participant_ids, default=0) + 1)],
                )
                for f in data.gold_frames
            ]
            if data.gold_frames is not None
            else None
        ),
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


@router.get(
    "/api/v1/matches/{match_id}/purge-preview",
    response_model=PurgePreview,
    summary="Prévia do expurgo de uma partida (nada é alterado)",
    responses={404: _NOT_FOUND, 422: _INVALID},
)
async def purge_preview_route(match_id: MatchId, request: Request) -> PurgePreview:
    return PurgePreview.model_validate(
        await purge_preview(request.app.state.session_factory, match_id)
    )


@router.delete(
    "/api/v1/matches/{match_id}",
    response_model=ImportJobAccepted,
    status_code=202,
    summary="Expurga a partida e seus derivados (CC-603)",
    description=(
        "Exige `confirm` igual ao id da partida. Cria um job PURGE consultável em "
        "/api/v1/imports/{jobId} (kind=PURGE). Idempotente: um expurgo ativo é devolvido; "
        "depois de concluído, repetir devolve o mesmo job."
    ),
    responses={
        404: _NOT_FOUND,
        422: {"model": ProblemDetail, "description": "Confirmação ausente ou diferente do id."},
    },
)
async def purge_match_route(
    match_id: MatchId,
    request: Request,
    confirm: Annotated[str, Query(min_length=1, max_length=64)],
) -> ImportJobAccepted:
    job = await request_purge(
        request.app.state.session_factory,
        match_id,
        confirm,
        request.app.state.settings.subject_hash_secret,
    )
    return ImportJobAccepted(
        job_id=job.id,
        status=ImportJobState(job.state.value),
        status_url=f"/api/v1/imports/{job.id}",
    )


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
        support_level=("DEMO" if MATCH_ID_DEMO_PATTERN.fullmatch(match.match_id) else "LIMITED"),
        support_reason=(
            "DEMO_DATA"
            if MATCH_ID_DEMO_PATTERN.fullmatch(match.match_id)
            else "PATCH_NOT_VALIDATED"
        ),
    )


async def _catalog_mapping_or_none(
    session_factory: async_sessionmaker[AsyncSession], path: Path
) -> CatalogMapping | None:
    """Mapping explícito lido do arquivo local (sem rede). Arquivo ausente ou inválido:
    a partida continua legível, só sem nomes/retratos (CC-208)."""
    try:
        if not path.is_file():
            return None
        return await load_persisted_mapping(session_factory, load_catalog_mapping(path))
    except SourceSchemaError:
        return None


def _to_participant(
    view: MatchParticipantFactsView, champions: Mapping[int, ChampionAssetData]
) -> MatchParticipant:
    asset = champions.get(view.champion_id)
    return MatchParticipant(
        participant_id=view.participant_id,
        champion_id=view.champion_id,
        team=view.team,
        role=view.role,
        final_items=view.final_items,
        final_trinket_item_id=view.final_trinket_item_id,
        win=view.score.win,
        kills=view.score.kills,
        deaths=view.score.deaths,
        assists=view.score.assists,
        total_minions_killed=view.score.total_minions_killed,
        neutral_minions_killed=view.score.neutral_minions_killed,
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
