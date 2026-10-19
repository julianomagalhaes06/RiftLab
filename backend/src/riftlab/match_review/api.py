"""GET /api/v1/matches/{matchId}/review (F1): revisão da partida de um participante."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request

from riftlab.errors import ProblemDetail
from riftlab.match_review.application import MatchReview as MatchReviewData
from riftlab.match_review.application import get_match_review
from riftlab.match_review.reference import MatchReviewReference, load_reference
from riftlab.match_review.win_probability import WinProbabilityModel, load_model
from riftlab.shared.api.common import MatchId
from riftlab.shared.api.match_review import (
    MatchReview,
    ReviewCounts,
    ReviewKeyMoment,
    ReviewLane,
    ReviewLaneMinute,
    ReviewLaneStats,
    ReviewMomentEvent,
    ReviewPurchase,
    ReviewReferenceItem,
    ReviewSkills,
    ReviewSkillShare,
    ReviewWinPoint,
    ReviewWinProbability,
)

router = APIRouter(tags=["matches"])


@lru_cache(maxsize=4)
def _reference(path: str, mtime: float) -> MatchReviewReference | None:
    return load_reference(Path(path))


@lru_cache(maxsize=4)
def _model(path: str, mtime: float) -> WinProbabilityModel | None:
    return load_model(Path(path))


def _mtime(path: str) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return -1.0


def _to_contract(data: MatchReviewData) -> MatchReview:
    return MatchReview(
        match_id=data.match_id,
        participant_id=data.participant_id,
        champion_id=data.champion_id,
        role=data.role,
        patch=data.patch,
        queue=data.queue,
        reference_version=data.reference_version,
        reference_inherited=data.reference_inherited,
        purchases=[
            ReviewPurchase(
                decision_id=p.decision_id,
                timestamp_ms=p.timestamp_ms,
                ordinal=p.ordinal,
                item_id=p.item_id,
                status=p.status,
                label=p.label,
                share=None if p.share is None else round(p.share, 4),
                rank=p.rank,
                support=p.support,
                reference_top=[
                    ReviewReferenceItem(item_id=i.item_id, share=round(i.share, 4))
                    for i in p.reference_top
                ],
            )
            for p in data.purchases
        ],
        skills=(
            ReviewSkills(
                priority=data.skills.priority,
                label=data.skills.label,
                share=None if data.skills.share is None else round(data.skills.share, 4),
                support=data.skills.support,
                most_common=[
                    ReviewSkillShare(priority=order, share=round(share, 4))
                    for order, share in data.skills.most_common
                ],
            )
            if data.skills is not None
            else None
        ),
        lane=(
            ReviewLane(
                opponent_id=data.lane.opponent_id,
                minutes=[
                    ReviewLaneMinute(
                        minute=c.minute,
                        own=ReviewLaneStats(**c.own),
                        opponent=ReviewLaneStats(**c.opponent),
                    )
                    for c in data.lane.comparisons
                ],
            )
            if data.lane is not None
            else None
        ),
        win_probability=(
            ReviewWinProbability(
                model_version=data.win.model_version,
                team=data.win.team,
                points=[ReviewWinPoint(timestamp_ms=t, probability=p) for t, p in data.win.points],
                key_moments=[
                    ReviewKeyMoment(
                        minute=m.minute,
                        delta=m.delta,
                        probability_after=m.probability_after,
                        events=[
                            ReviewMomentEvent(
                                kind=e.kind,
                                team=e.team,
                                involves_participant=e.involves_participant,
                            )
                            for e in m.events
                        ],
                    )
                    for m in data.win.key_moments
                ],
            )
            if data.win is not None
            else None
        ),
        win_probability_unavailable=data.win_unavailable_reason,  # type: ignore[arg-type]
        counts=ReviewCounts.model_validate(data.counts),
    )


@router.get(
    "/api/v1/matches/{match_id}/review",
    response_model=MatchReview,
    summary="Revisão da partida de um participante (F1)",
    description=(
        "Compras do 1º ao 5º item principal rotuladas frente à referência do mesmo campeão, "
        "rota e etapa (comum, alternativa, incomum, sem referência), ordem de habilidades, "
        "fase de rotas contra o oponente direto e chance de vitória por minuto com "
        "momentos-chave. Popularidade não é qualidade e a chance é correlacional. Só leitura."
    ),
    responses={404: {"model": ProblemDetail, "description": "Partida ou participante ausente."}},
)
async def match_review(
    match_id: MatchId,
    request: Request,
    participant_id: Annotated[int, Query(alias="participantId", ge=1, le=10)],
) -> MatchReview:
    settings = request.app.state.settings
    reference_path = settings.match_review_reference_path
    model_path = settings.win_probability_model_path
    data = await get_match_review(
        request.app.state.session_factory,
        match_id,
        participant_id,
        scope_path=Path(settings.scope_path),
        reference=_reference(reference_path, _mtime(reference_path)),
        model=_model(model_path, _mtime(model_path)),
    )
    return _to_contract(data)
