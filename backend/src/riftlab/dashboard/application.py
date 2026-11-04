from __future__ import annotations

from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.infrastructure.catalog_mapping import load_catalog_mapping
from riftlab.catalog.patches import persisted_mapping
from riftlab.champions.application import champion_assets
from riftlab.config import Settings
from riftlab.dashboard.api import (
    Dashboard,
    DashboardChampion,
    DashboardMatch,
    DashboardProgress,
    DashboardQueue,
)
from riftlab.decisions.models import DecisionPointModel
from riftlab.ingestion.models import MatchModel, ParticipantModel
from riftlab.learning.models import LearningAttemptModel as Attempt
from riftlab.ranked.domain import SOLO_QUEUE, account_key
from riftlab.ranked.models import RankedObservationModel as Observation
from riftlab.shared.api.matches import ChampionAsset, MatchParticipant
from riftlab.shared.db import read_only_unit_of_work
from riftlab.shared.errors import SourceSchemaError
from riftlab.shared.own_account import CONFIGURED, OwnAccount, resolve

QUEUE_IDS = {"RANKED_SOLO_5x5": 420, "RANKED_FLEX_SR": 440}
DASHBOARD_QUEUES = (420, 440)


def summarize(rows: list[tuple[MatchModel, ParticipantModel]]) -> dict[str, int | float | None]:
    """Ausência não é zero; KDA usa somas, não a média dos quocientes por partida."""
    wins = [p.win for _, p in rows if p.win is not None]
    scores = [p for _, p in rows if all(v is not None for v in (p.kills, p.deaths, p.assists))]
    kills = sum(p.kills or 0 for p in scores)
    deaths = sum(p.deaths or 0 for p in scores)
    assists = sum(p.assists or 0 for p in scores)
    return {
        "sampled_matches": len(rows),
        "win_samples": len(wins),
        "wins": sum(wins),
        "win_rate": sum(wins) / len(wins) if wins else None,
        "kda_samples": len(scores),
        "kda": (kills + assists) / max(1, deaths) if scores else None,
        "distinct_champions": len({p.champion_id for _, p in rows}),
    }


async def dashboard(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    window: int,
    origin: Literal["REAL", "DEMO"],
    queue: int | None = None,
    account: OwnAccount = CONFIGURED,
) -> Dashboard:
    """`account`: conta configurada (padrão) ou a vinculada ao usuário da sessão (card #13)."""
    mapping = None
    mapping_path = Path(settings.catalog_mappings_path)
    try:
        overrides = load_catalog_mapping(mapping_path) if mapping_path.is_file() else None
    except SourceSchemaError:
        overrides = None
    key = None
    if origin == "REAL":
        resolved = resolve(settings, account)
        key = account_key(resolved, settings.subject_hash_secret) if resolved else None
    demo_match = MatchModel.match_id.like("DEMO-%")
    async with read_only_unit_of_work(factory) as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        if overrides is not None:
            mapping = await persisted_mapping(session, overrides)
        progress_row = (
            await session.execute(
                select(
                    func.count(Attempt.id),
                    func.count(Attempt.id).filter(Attempt.status == "REVEALED"),
                    func.count(Attempt.id).filter(Attempt.reflection.is_not(None)),
                    func.count(func.distinct(DecisionPointModel.match_id)),
                )
                .select_from(Attempt)
                .join(DecisionPointModel, Attempt.decision_id == DecisionPointModel.id)
                .join(MatchModel, DecisionPointModel.match_id == MatchModel.match_id)
                .where(demo_match if origin == "DEMO" else ~demo_match)
            )
        ).one()
        progress = DashboardProgress(
            attempts=progress_row[0],
            revealed=progress_row[1],
            reflections=progress_row[2],
            matches=progress_row[3],
        )
        subject = None
        if key:
            # Sem fallback silencioso para vínculo de uma observação antiga.
            observation = await session.scalar(
                select(Observation)
                .where(Observation.account_key == key, Observation.queue == SOLO_QUEUE)
                .order_by(Observation.captured_at.desc(), Observation.id.desc())
                .limit(1)
            )
            if observation and observation.origin == "RIOT_LEAGUE_V4":
                subject = observation.subject_hash
        available = origin == "DEMO" or subject is not None
        rows: list[tuple[MatchModel, ParticipantModel]] = []
        total = 0
        chosen = queue or DASHBOARD_QUEUES[0]
        totals: dict[int, int] = dict.fromkeys(DASHBOARD_QUEUES, 0)
        if available:
            own = (
                select(ParticipantModel.match_id)
                .where(
                    ParticipantModel.participant_id == 1
                    if origin == "DEMO"
                    else ParticipantModel.subject_hash == subject
                )
                .group_by(ParticipantModel.match_id)
                .having(func.count() == 1)
            )
            ended = MatchModel.start_at + MatchModel.duration_seconds * text("INTERVAL '1 second'")
            conditions = (
                MatchModel.match_id.in_(own),
                demo_match if origin == "DEMO" else ~demo_match,
                ParticipantModel.participant_id == 1
                if origin == "DEMO"
                else ParticipantModel.subject_hash == subject,
            )
            latest: dict[int, Any] = {}
            for queue_id in DASHBOARD_QUEUES:
                count, last = (
                    await session.execute(
                        select(func.count(), func.max(ended))
                        .select_from(MatchModel)
                        .join(ParticipantModel, ParticipantModel.match_id == MatchModel.match_id)
                        .where(*conditions, MatchModel.queue == queue_id)
                    )
                ).one()
                totals[queue_id] = int(count or 0)
                latest[queue_id] = last
            if queue is None:
                # Sem fila pedida: a da partida própria mais recente (empate: Solo/Duo).
                dated = [(latest[q], -i, q) for i, q in enumerate(DASHBOARD_QUEUES) if latest[q]]
                chosen = max(dated)[2] if dated else DASHBOARD_QUEUES[0]
            query = (
                select(MatchModel, ParticipantModel)
                .join(ParticipantModel, ParticipantModel.match_id == MatchModel.match_id)
                .where(*conditions, MatchModel.queue == chosen)
            )
            total = int(
                await session.scalar(select(func.count()).select_from(query.subquery())) or 0
            )
            rows = [
                (m, p)
                for m, p in (
                    await session.execute(
                        query.order_by(ended.desc(), MatchModel.match_id.desc()).limit(window)
                    )
                ).all()
            ]
        assets: dict[tuple[str, int], ChampionAsset] = {}
        for patch in {m.patch for m, _ in rows}:
            part = [(m, p) for m, p in rows if m.patch == patch]
            found = await champion_assets(
                session, mapping, part[0][0].match_id, patch, [p.champion_id for _, p in part]
            )
            for champion_id, asset in found.items():
                assets[patch, champion_id] = ChampionAsset(
                    name=asset.name,
                    data_dragon_version=asset.data_dragon_version,
                    image_file=asset.image_file,
                )
    recent = [
        DashboardMatch(
            match_id=m.match_id,
            ended_at=m.start_at + timedelta(seconds=m.duration_seconds),
            duration_seconds=m.duration_seconds,
            participant=MatchParticipant(
                participant_id=p.participant_id,
                champion_id=p.champion_id,
                team=p.team,
                role=p.role,
                win=p.win,
                kills=p.kills,
                deaths=p.deaths,
                assists=p.assists,
                total_minions_killed=p.total_minions_killed,
                neutral_minions_killed=p.neutral_minions_killed,
                champion=assets.get((m.patch, p.champion_id)),
            ),
        )
        for m, p in rows[:5]
    ]
    counts = Counter(p.champion_id for _, p in rows)
    champions = [
        DashboardChampion(
            champion_id=champion_id,
            matches=count,
            champion=next(
                (
                    assets[m.patch, champion_id]
                    for m, p in rows
                    if p.champion_id == champion_id and (m.patch, champion_id) in assets
                ),
                None,
            ),
        )
        for champion_id, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:3]
    ]
    return Dashboard.model_validate(
        dict(
            origin=origin,
            status="IDENTITY_UNAVAILABLE" if not available else "READY" if rows else "EMPTY",
            window=window,
            queue=chosen,
            queue_selection="AUTO" if queue is None else "EXPLICIT",
            queues=[DashboardQueue(queue=q, total_matches=totals[q]) for q in DASHBOARD_QUEUES],
            total_matches=total,
            **summarize(rows),
            champions=champions,
            recent_matches=recent,
            progress=progress,
        )
    )
