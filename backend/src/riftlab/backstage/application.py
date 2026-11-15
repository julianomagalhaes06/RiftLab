"""Bastidores: o que o RiftLab está fazendo agora, lido do banco (sem chamar a Riot).

Chamadas recentes à Riot (tipo de consulta, região, resultado e tempo), o estado da chave,
a fila do worker, as importações e sincronizações recentes e o tamanho do acervo. Nada aqui
traz Riot ID, PUUID ou e-mail; IDs de partida aparecem só nas importações, para o
administrador conferir uma partida específica.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.auth.models import RiotLinkModel, UserModel
from riftlab.backstage.models import RiotApiCallModel as Call
from riftlab.decisions.models import DecisionPointModel
from riftlab.history_sync.models import HistorySyncRunModel as Run
from riftlab.ingestion.models import ImportJobModel as Job
from riftlab.ingestion.models import MatchModel, ParticipantModel
from riftlab.learning.models import LearningAttemptModel
from riftlab.players.models import PlayerProfileModel
from riftlab.shared.db import read_only_unit_of_work
from riftlab.timeline.models import TimelineEventModel

SessionFactory = async_sessionmaker[AsyncSession]
RECENT_CALLS = 40
RECENT_JOBS = 12
RECENT_SYNCS = 8
ACTIVE_JOB_STATES = ("QUEUED", "RUNNING", "RETRY_WAIT")


def _key_status(last: Call | None) -> str:
    """Estado da chave pela última resposta conclusiva da Riot nas últimas 24 h."""
    if last is None:
        return "UNKNOWN"
    return "OK" if last.outcome == "HTTP_200" else "REJECTED"


async def _count(session: AsyncSession, statement: Any) -> int:
    return int(await session.scalar(statement) or 0)


async def snapshot(factory: SessionFactory, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(days=1)
    real_match = ~MatchModel.match_id.like("DEMO-%")
    async with read_only_unit_of_work(factory) as session:
        calls = (
            await session.scalars(
                select(Call).order_by(Call.at.desc(), Call.id.desc()).limit(RECENT_CALLS)
            )
        ).all()
        total, ok, auth, limited, average = (
            await session.execute(
                select(
                    func.count(),
                    func.count().filter(Call.outcome == "HTTP_200"),
                    func.count().filter(Call.outcome == "HTTP_AUTH"),
                    func.count().filter(Call.outcome == "HTTP_429"),
                    func.avg(Call.duration_ms),
                ).where(Call.at >= hour_ago)
            )
        ).one()
        by_kind = (
            await session.execute(
                select(Call.kind, func.count(), func.avg(Call.duration_ms))
                .where(Call.at >= hour_ago)
                .group_by(Call.kind)
                .order_by(func.count().desc(), Call.kind)
            )
        ).all()
        last_conclusive = (
            await session.scalars(
                select(Call)
                .where(Call.at >= day_ago, Call.outcome.in_(("HTTP_200", "HTTP_AUTH")))
                .order_by(Call.at.desc(), Call.id.desc())
                .limit(1)
            )
        ).first()
        last_worker_call = await session.scalar(
            select(func.max(Call.at)).where(Call.process == "worker")
        )
        queue = (
            await session.execute(
                select(Job.kind, Job.state, func.count())
                .where(Job.state.in_(ACTIVE_JOB_STATES))
                .group_by(Job.kind, Job.state)
            )
        ).all()
        failed_today = await _count(
            session,
            select(func.count()).where(Job.state == "FAILED", Job.updated_at >= day_ago),
        )
        succeeded_today = await _count(
            session,
            select(func.count()).where(Job.state == "SUCCEEDED", Job.updated_at >= day_ago),
        )
        jobs = (
            await session.scalars(
                select(Job).order_by(Job.updated_at.desc(), Job.id.desc()).limit(RECENT_JOBS)
            )
        ).all()
        syncs = (
            await session.scalars(
                select(Run).order_by(Run.requested_at.desc(), Run.id.desc()).limit(RECENT_SYNCS)
            )
        ).all()
        counters = {
            "matches": await _count(session, select(func.count()).where(real_match)),
            "participants": await _count(
                session,
                select(func.count()).where(~ParticipantModel.match_id.like("DEMO-%")),
            ),
            "timelineEvents": await _count(
                session,
                select(func.count()).where(~TimelineEventModel.match_id.like("DEMO-%")),
            ),
            "decisions": await _count(
                session,
                select(func.count()).where(
                    DecisionPointModel.eligible, ~DecisionPointModel.match_id.like("DEMO-%")
                ),
            ),
            "studyAttempts": await _count(session, select(func.count(LearningAttemptModel.id))),
            "playersSearched": await _count(
                session, select(func.count()).select_from(PlayerProfileModel)
            ),
            "users": await _count(session, select(func.count(UserModel.id))),
            "linkedAccounts": await _count(
                session, select(func.count()).select_from(RiotLinkModel)
            ),
        }
    queue_counts: dict[str, dict[str, int]] = {}
    for kind, state, count in queue:
        queue_counts.setdefault(kind, {})[state] = int(count)
    return {
        "generatedAt": now,
        "riotKey": {
            "status": _key_status(last_conclusive),
            "checkedAt": last_conclusive.at if last_conclusive else None,
        },
        "lastHour": {
            "calls": int(total),
            "succeeded": int(ok),
            "authFailures": int(auth),
            "rateLimited": int(limited),
            "averageMs": round(float(average)) if average is not None else None,
            "byKind": [
                {"kind": kind, "calls": int(count), "averageMs": round(float(avg or 0))}
                for kind, count, avg in by_kind
            ],
        },
        "calls": [
            {
                "id": call.id,
                "at": call.at,
                "process": call.process,
                "kind": call.kind,
                "region": call.region,
                "outcome": call.outcome,
                "statusCode": call.status_code,
                "durationMs": call.duration_ms,
                "attempt": call.attempt,
            }
            for call in calls
        ],
        "worker": {
            "lastRiotCallAt": last_worker_call,
            "queue": [
                {"kind": kind, "states": states} for kind, states in sorted(queue_counts.items())
            ],
            "succeededToday": succeeded_today,
            "failedToday": failed_today,
        },
        "jobs": [
            {
                "matchId": job.requested_match_id,
                "kind": job.kind,
                "mode": job.mode,
                "state": job.state,
                "attempts": job.attempts,
                "errorCode": job.error_code,
                "createdAt": job.created_at,
                "updatedAt": job.updated_at,
                "fromSync": job.sync_run_id is not None,
            }
            for job in jobs
        ],
        "syncs": [
            {
                "state": run.state,
                "requestedAt": run.requested_at,
                "finishedAt": run.finished_at,
                "listed": run.listed,
                "newEnqueued": run.new_enqueued,
                "alreadyKnown": run.already_known,
                "errorCode": run.error_code,
                "steps": run.steps,
            }
            for run in syncs
        ],
        "counters": counters,
    }
