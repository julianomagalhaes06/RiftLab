"""Revisão da partida (F1, CC-1003/CC-1004): compras rotuladas frente à referência do mesmo
campeão, rota e etapa; ordem de habilidades; fase de rotas contra o oponente direto; chance de
vitória por minuto com momentos-chave. Só leitura (transação READ ONLY), nada pessoal sai daqui.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.decisions.application import resolve_current_versions
from riftlab.decisions.domain import (
    DECISION_SELECTION_VERSION,
    DecisionError,
    StoredDecisionPoint,
)
from riftlab.decisions.models import DecisionExtractionModel, DecisionPointModel
from riftlab.decisions.repository import SqlAlchemyDecisionRepository
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.match_review.lane import (
    LaneComparison,
    direct_opponent,
    has_lane_stats,
    lane_comparison,
    parse_frames,
)
from riftlab.match_review.reference import (
    MatchReviewReference,
    PurchaseLabel,
    SkillLabel,
    is_review_ready,
    skill_priority,
)
from riftlab.match_review.win_probability import (
    BLUE,
    WinProbabilityModel,
    minute_features,
    team_events,
)
from riftlab.matches.timeline import Kill, Objective, get_match_timeline
from riftlab.shared.db import read_only_unit_of_work
from riftlab.shared.errors import NotFoundError
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
from riftlab.timeline.empirical_rules import SUPPORT_FINAL_ITEM_IDS
from riftlab.timeline.models import MatchFramesModel

SessionFactory = async_sessionmaker[AsyncSession]
MAX_ORDINAL = 5
# Escolha final do item de Apoio: item de função, não item principal (fica fora da revisão).
ROLE_ITEM_IDS = SUPPORT_FINAL_ITEM_IDS
MAX_POINTS = 500
KEY_MOMENTS = 3
KEY_MOMENT_MIN_DELTA = 0.08
# Partida sem decisões preparadas: a revisão sai sem compras (mesmos códigos da lista).
_NOT_PREPARED = frozenset({"RECONSTRUCTION_REQUIRED", "ORIGIN_UNRESOLVED"})


@dataclass(frozen=True, slots=True)
class ReferenceItem:
    item_id: int
    share: float


@dataclass(frozen=True, slots=True)
class ReviewedPurchase:
    decision_id: str
    timestamp_ms: int
    ordinal: int
    item_id: int
    status: Literal["READY", "NOT_READY"]
    label: PurchaseLabel | None
    share: float | None
    rank: int | None
    support: int | None
    reference_top: tuple[ReferenceItem, ...]


@dataclass(frozen=True, slots=True)
class SkillReview:
    priority: str | None
    label: SkillLabel
    share: float | None
    support: int
    most_common: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class LaneReview:
    opponent_id: int
    comparisons: tuple[LaneComparison, ...]


@dataclass(frozen=True, slots=True)
class MomentEvent:
    kind: str
    team: int | None
    involves_participant: bool


@dataclass(frozen=True, slots=True)
class KeyMoment:
    minute: int
    delta: float
    probability_after: float
    events: tuple[MomentEvent, ...]


@dataclass(frozen=True, slots=True)
class WinReview:
    model_version: str
    team: int
    points: tuple[tuple[int, float], ...]
    key_moments: tuple[KeyMoment, ...]


@dataclass(frozen=True, slots=True)
class MatchReview:
    match_id: str
    participant_id: int
    champion_id: int
    role: str | None
    patch: str
    queue: int
    reference_version: str | None
    reference_inherited: bool
    purchases: tuple[ReviewedPurchase, ...]
    skills: SkillReview | None
    lane: LaneReview | None
    win: WinReview | None
    win_unavailable_reason: str | None = None
    counts: dict[str, int] = field(default_factory=dict)


def _label_purchases(
    points: Sequence[StoredDecisionPoint],
    reference: MatchReviewReference | None,
    champion_id: int,
    role: str | None,
) -> list[ReviewedPurchase]:
    out: list[ReviewedPurchase] = []
    for point in points:
        ordinal = point.main_item_ordinal
        if ordinal is None or ordinal > MAX_ORDINAL or point.target_item_id in ROLE_ITEM_IDS:
            continue
        ready = role is not None and is_review_ready(point.reasons)
        label: PurchaseLabel | None = None
        share = rank = support = None
        top: tuple[ReferenceItem, ...] = ()
        if ready and reference is not None and role is not None:
            label, share, rank, cell = reference.label_purchase(
                champion_id, role, ordinal, point.target_item_id
            )
            if cell is not None:
                support = cell.n
                top = tuple(ReferenceItem(i.item_id, i.count / cell.n) for i in cell.items[:3])
        out.append(
            ReviewedPurchase(
                decision_id=str(point.id),
                timestamp_ms=point.event_timestamp_ms,
                ordinal=ordinal,
                item_id=point.target_item_id,
                status="READY" if ready else "NOT_READY",
                label=label,
                share=share,
                rank=rank,
                support=support,
                reference_top=top,
            )
        )
    return out


def key_moments(
    points: Sequence[tuple[int, float]],
    kills: Sequence[Kill],
    objectives: Sequence[Objective],
    participant_id: int,
    teams: Mapping[int, int] | None = None,
) -> tuple[KeyMoment, ...]:
    """As maiores variações (do ponto de vista do time do participante) entre quadros
    seguidos, com os abates e objetivos daquele intervalo. Correlação, nunca causa."""
    deltas = []
    for (prev_ms, prev_p), (ms, p) in zip(points, points[1:], strict=False):
        deltas.append((abs(p - prev_p), prev_ms, ms, p - prev_p, p))
    chosen = sorted(
        (d for d in deltas if d[0] >= KEY_MOMENT_MIN_DELTA), key=lambda d: d[0], reverse=True
    )[:KEY_MOMENTS]
    moments = []
    for _, start, end, delta, after in sorted(chosen, key=lambda d: d[1]):
        events: list[MomentEvent] = []
        for kill in kills:
            if start < kill.timestamp_ms <= end:
                involved = participant_id in (kill.killer_id, kill.victim_id, *kill.assisting_ids)
                kind = (
                    "DEATH"
                    if kill.victim_id == participant_id
                    else "KILL"
                    if kill.killer_id == participant_id
                    else "ASSIST"
                    if participant_id in kill.assisting_ids
                    else "CHAMPION_KILL"
                )
                killer_team = (teams or {}).get(kill.killer_id)
                if killer_team is None:
                    victim_team = (teams or {}).get(kill.victim_id)
                    killer_team = {100: 200, 200: 100}.get(victim_team) if victim_team else None
                events.append(MomentEvent(kind, killer_team, involved))
        for objective in objectives:
            if start < objective.timestamp_ms <= end:
                events.append(MomentEvent(objective.kind, objective.team, False))
        moments.append(KeyMoment(end // 60_000, round(delta, 4), round(after, 4), tuple(events)))
    return tuple(moments)


async def review_purchase_counts(
    session: AsyncSession, local_ids: Mapping[str, int]
) -> dict[str, tuple[int, int]]:
    """Selo do Histórico: (compras prontas para a revisão, compras do 1º ao 5º item) do
    participante local, na extração mais recente das versões correntes de cada partida."""
    if not local_ids:
        return {}
    extractions = (
        await session.execute(
            select(DecisionExtractionModel.id, DecisionExtractionModel.match_id)
            .where(
                DecisionExtractionModel.match_id.in_(list(local_ids)),
                DecisionExtractionModel.reconstruction_version == RECONSTRUCTION_ALGORITHM_VERSION,
                DecisionExtractionModel.selection_version == DECISION_SELECTION_VERSION,
            )
            .order_by(DecisionExtractionModel.created_at.desc())
        )
    ).all()
    latest: dict[str, uuid.UUID] = {}
    for extraction_id, match_id in extractions:
        latest.setdefault(match_id, extraction_id)
    if not latest:
        return {}
    rows = (
        await session.execute(
            select(
                DecisionPointModel.match_id,
                DecisionPointModel.participant_id,
                DecisionPointModel.reasons,
            ).where(
                DecisionPointModel.extraction_id.in_(list(latest.values())),
                DecisionPointModel.main_item_ordinal.between(1, MAX_ORDINAL),
                DecisionPointModel.target_item_id.not_in(sorted(ROLE_ITEM_IDS)),
            )
        )
    ).all()
    counts: dict[str, list[int]] = {match_id: [0, 0] for match_id in latest}
    for match_id, participant_id, reasons in rows:
        if local_ids.get(match_id) != participant_id:
            continue
        counts[match_id][1] += 1
        counts[match_id][0] += int(is_review_ready(reasons))
    return {match_id: (ready, total) for match_id, (ready, total) in counts.items()}


async def get_match_review(
    session_factory: SessionFactory,
    match_id: str,
    participant_id: int,
    *,
    scope_path: Path,
    reference: MatchReviewReference | None,
    model: WinProbabilityModel | None,
) -> MatchReview:
    timeline = await get_match_timeline(session_factory, match_id)
    async with read_only_unit_of_work(session_factory) as session:
        ingestion = SqlAlchemyIngestionRepository(session)
        match = await ingestion.get_match(match_id)
        if match is None:
            raise NotFoundError(f"match {match_id!r} não encontrado")
        participants = await ingestion.list_participants(match_id)
        participant = next((p for p in participants if p.participant_id == participant_id), None)
        if participant is None:
            raise NotFoundError("participante fora da partida")
        frames_row = await session.get(MatchFramesModel, match_id)
        points: list[StoredDecisionPoint] = []
        try:
            current = await resolve_current_versions(session, match_id, scope_path)
            decisions = SqlAlchemyDecisionRepository(session)
            extraction = await decisions.get_extraction(match_id, current.versions)
            if extraction is not None:
                points = await decisions.list_points_page(
                    extraction.id, participant_id, None, MAX_POINTS
                )
        except DecisionError as exc:
            if exc.code not in _NOT_PREPARED:
                raise

    teams = {p.participant_id: p.team for p in participants}
    roles = {p.participant_id: p.role for p in participants}
    role = participant.role
    purchases = _label_purchases(points, reference, participant.champion_id, role)

    skills: SkillReview | None = None
    own = next((t for t in timeline.participants if t.participant_id == participant_id), None)
    if own is not None and reference is not None and role is not None:
        priority = skill_priority([(s.skill_slot, s.level_up_type) for s in own.skills])
        label, share, orders, n = reference.label_skills(participant.champion_id, role, priority)
        skills = SkillReview(
            priority,
            label,
            share,
            n,
            tuple((order, count / n) for order, count in orders[:3]) if n else (),
        )

    frames = parse_frames(frames_row.frames) if frames_row is not None else []
    lane: LaneReview | None = None
    opponent = direct_opponent(participant_id, roles, teams)
    if opponent is not None and has_lane_stats(frames):
        lane = LaneReview(opponent, tuple(lane_comparison(frames, participant_id, opponent)))

    win: WinReview | None = None
    unavailable: str | None = None
    team = teams.get(participant_id, BLUE)
    if model is None:
        unavailable = "MODEL_UNAVAILABLE"
    elif not model.gate_passed:
        unavailable = "MODEL_GATE_FAILED"
    elif not has_lane_stats(frames):
        unavailable = "FRAMES_WITHOUT_XP"
    else:
        rows = minute_features(
            [f.stat_frame() for f in frames],
            teams,
            team_events(timeline.kills, timeline.objectives, teams),
        )
        series = []
        for row in rows:
            blue = model.blue_probability(row)
            series.append((row.timestamp_ms, round(blue if team == BLUE else 1 - blue, 4)))
        win = WinReview(
            model.model_version,
            team,
            tuple(series),
            key_moments(series, timeline.kills, timeline.objectives, participant_id, teams),
        )

    ready = [p for p in purchases if p.status == "READY"]
    counts = {
        "purchases": len(purchases),
        "ready": len(ready),
        "common": sum(p.label == "COMMON" for p in ready),
        "alternative": sum(p.label == "ALTERNATIVE" for p in ready),
        "uncommon": sum(p.label == "UNCOMMON" for p in ready),
        "noReference": sum(p.label in (None, "NO_REFERENCE") for p in ready),
    }
    inherited = reference is not None and (
        reference.patch != match.patch or reference.queue_id != match.queue
    )
    return MatchReview(
        match_id=match_id,
        participant_id=participant_id,
        champion_id=participant.champion_id,
        role=role,
        patch=match.patch,
        queue=match.queue,
        reference_version=reference.reference_version if reference is not None else None,
        reference_inherited=inherited,
        purchases=tuple(purchases),
        skills=skills,
        lane=lane,
        win=win,
        win_unavailable_reason=unavailable,
        counts=counts,
    )
