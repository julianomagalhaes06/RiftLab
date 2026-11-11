"""Gamificação do estudo (11/11/2026): XP e nível, sequência diária, conquistas e missões da
semana, todos calculados das tentativas de estudo reais (learning_attempts), sem tabela nova e
sem nada inventado. Não é nota nem estimativa de habilidade: mede só o quanto a pessoa estudou.

Regras:
- Uma decisão conta como estudada quando a tentativa foi respondida (não pulada) ou revelada.
- XP por decisão (a melhor tentativa de cada uma; repetir a mesma decisão não soma de novo):
  estudar 20, revelar a comparação +10, revelar sem usar dica +10, escrever a reflexão +15.
- Nível: do nível L para L+1 são 100 + 50 × (L − 1) de XP (100, 150, 200, ...).
- Sequência: dias seguidos (horário de Brasília) com ao menos uma decisão estudada; vale até o
  fim do dia seguinte ao último estudo.
- Missões: 3 por semana (segunda a domingo), escolhidas de forma fixa pelo número da semana.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.decisions.models import DecisionPointModel
from riftlab.ingestion.models import ParticipantModel
from riftlab.learning.models import LearningAttemptModel

TIMEZONE = ZoneInfo("America/Sao_Paulo")
XP_STUDY = 20
XP_REVEAL = 10
XP_NO_HINT = 10
XP_REFLECTION = 15


@dataclass(frozen=True, slots=True)
class AttemptFact:
    """O que a gamificação precisa de uma tentativa (já com a partida e o resultado)."""

    decision_id: uuid.UUID
    match_id: str
    participant_id: int
    created_at: datetime
    status: str
    skipped: bool
    hint_level: int
    reflected: bool
    lost: bool  # a decisão é de uma partida que o participante perdeu

    @property
    def studied(self) -> bool:
        return (self.status != "OPEN" and not self.skipped) or self.status == "REVEALED"

    @property
    def revealed(self) -> bool:
        return self.status == "REVEALED"

    @property
    def xp(self) -> int:
        if not self.studied:
            return 0
        points = XP_STUDY
        if self.revealed:
            points += XP_REVEAL
            if self.hint_level == 0:
                points += XP_NO_HINT
        if self.reflected:
            points += XP_REFLECTION
        return points

    @property
    def day(self) -> date:
        moment = self.created_at if self.created_at.tzinfo else self.created_at.replace(tzinfo=UTC)
        return moment.astimezone(TIMEZONE).date()


def level_for(xp: int) -> tuple[int, int, int]:
    """(nível, XP já feito no nível atual, XP que o nível atual pede para subir)."""
    level, remaining = 1, xp
    need = 100
    while remaining >= need:
        remaining -= need
        level += 1
        need = 100 + 50 * (level - 1)
    return level, remaining, need


def _streaks(days: set[date], today: date) -> tuple[int, int, bool]:
    """(sequência atual, melhor sequência, estudou hoje)."""
    if not days:
        return 0, 0, False
    ordered = sorted(days)
    best = run = 1
    for previous, current in zip(ordered, ordered[1:], strict=False):
        run = run + 1 if (current - previous).days == 1 else 1
        best = max(best, run)
    studied_today = today in days
    anchor = today if studied_today else today - timedelta(days=1)
    streak = 0
    while anchor in days:
        streak += 1
        anchor -= timedelta(days=1)
    return streak, best, studied_today


def _best_per_decision(facts: Iterable[AttemptFact]) -> dict[uuid.UUID, AttemptFact]:
    best: dict[uuid.UUID, AttemptFact] = {}
    for fact in facts:
        if fact.studied and (fact.decision_id not in best or fact.xp > best[fact.decision_id].xp):
            best[fact.decision_id] = fact
    return best


@dataclass(frozen=True, slots=True)
class _Goal:
    key: str
    title: str
    description: str
    target: int


ACHIEVEMENTS: tuple[_Goal, ...] = (
    _Goal("first_study", "Primeira decisão", "Estude a sua primeira decisão de compra.", 1),
    _Goal("first_reflection", "Primeira reflexão", "Escreva a reflexão depois de revelar.", 1),
    _Goal("study_10", "Estudioso", "Estude 10 decisões diferentes.", 10),
    _Goal("study_25", "Analista", "Estude 25 decisões diferentes.", 25),
    _Goal("study_50", "Mestre das compras", "Estude 50 decisões diferentes.", 50),
    _Goal("no_hint_5", "Sem dicas", "Revele 5 comparações sem usar nenhuma dica.", 5),
    _Goal("streak_3", "Três dias seguidos", "Estude em 3 dias seguidos.", 3),
    _Goal("streak_7", "Semana completa", "Estude em 7 dias seguidos.", 7),
    _Goal(
        "full_match", "Partida dominada", "Estude todas as decisões analisáveis de uma partida.", 1
    ),
    _Goal("learn_from_loss", "Aprender com a derrota", "Estude uma decisão de uma derrota.", 1),
)

MISSIONS: tuple[_Goal, ...] = (
    _Goal("study_3", "Estude 3 decisões", "Três decisões de compra diferentes nesta semana.", 3),
    _Goal("reflect_2", "Escreva 2 reflexões", "Depois de revelar, conte o que mudaria.", 2),
    _Goal("no_hint_2", "Revele 2 sem dica", "Duas comparações reveladas sem usar dicas.", 2),
    _Goal("loss_1", "Estude uma derrota", "Uma decisão de uma partida perdida.", 1),
    _Goal("matches_2", "Duas partidas", "Estude decisões de 2 partidas diferentes.", 2),
    _Goal("reveal_3", "Revele 3 comparações", "Três comparações reveladas nesta semana.", 3),
)


def _week_missions(week_start: date) -> list[_Goal]:
    """3 missões fixas por semana: a semana ISO escolhe o deslocamento na lista."""
    offset = week_start.isocalendar().week % len(MISSIONS)
    return [MISSIONS[(offset + step * 2) % len(MISSIONS)] for step in range(3)]


def _mission_progress(goal: _Goal, facts: Sequence[AttemptFact]) -> int:
    best = _best_per_decision(facts)
    if goal.key == "study_3":
        return len(best)
    if goal.key == "reflect_2":
        return sum(f.reflected for f in best.values())
    if goal.key == "no_hint_2":
        return sum(f.revealed and f.hint_level == 0 for f in best.values())
    if goal.key == "loss_1":
        return sum(f.lost for f in best.values())
    if goal.key == "matches_2":
        return len({f.match_id for f in best.values()})
    if goal.key == "reveal_3":
        return sum(f.revealed for f in best.values())
    return 0


def summarize(
    facts: Sequence[AttemptFact],
    *,
    now: datetime,
    eligible_by_match: dict[tuple[str, int], set[uuid.UUID]],
    match_id: str | None = None,
) -> dict[str, Any]:
    best = _best_per_decision(facts)
    xp = sum(f.xp for f in best.values())
    level, into_level, level_need = level_for(xp)
    today = now.astimezone(TIMEZONE).date()
    current, best_streak, studied_today = _streaks({f.day for f in best.values()}, today)

    studied_by_match: dict[tuple[str, int], set[uuid.UUID]] = {}
    for decision, fact in best.items():
        studied_by_match.setdefault((fact.match_id, fact.participant_id), set()).add(decision)
    full_matches = sum(
        1
        for key, eligible in eligible_by_match.items()
        if eligible and eligible <= studied_by_match.get(key, set())
    )
    values = {
        "first_study": len(best),
        "first_reflection": sum(f.reflected for f in best.values()),
        "study_10": len(best),
        "study_25": len(best),
        "study_50": len(best),
        "no_hint_5": sum(f.revealed and f.hint_level == 0 for f in best.values()),
        "streak_3": best_streak,
        "streak_7": best_streak,
        "full_match": full_matches,
        "learn_from_loss": sum(f.lost for f in best.values()),
    }
    achievements = [
        {
            "key": goal.key,
            "title": goal.title,
            "description": goal.description,
            "progress": min(values[goal.key], goal.target),
            "target": goal.target,
            "unlocked": values[goal.key] >= goal.target,
        }
        for goal in ACHIEVEMENTS
    ]

    week_start = today - timedelta(days=today.weekday())
    week_facts = [f for f in facts if f.day >= week_start]
    missions = [
        {
            "key": goal.key,
            "title": goal.title,
            "description": goal.description,
            "progress": min(_mission_progress(goal, week_facts), goal.target),
            "target": goal.target,
            "completed": _mission_progress(goal, week_facts) >= goal.target,
        }
        for goal in _week_missions(week_start)
    ]

    match = None
    if match_id is not None:
        studied = {d: f for d, f in best.items() if f.match_id == match_id}
        match = {
            "matchId": match_id,
            "studiedDecisionIds": sorted(str(d) for d in studied),
            "xp": sum(f.xp for f in studied.values()),
        }
    return {
        "xp": xp,
        "level": level,
        "levelXp": into_level,
        "levelTarget": level_need,
        "studiedDecisions": len(best),
        "streak": {"current": current, "best": best_streak, "studiedToday": studied_today},
        "achievements": achievements,
        "missions": missions,
        "weekStart": week_start.isoformat(),
        "match": match,
    }


async def gamification(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    owner: uuid.UUID | None = None,
    match_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Só as tentativas do usuário da sessão (como o progresso, #11); sem sessão, as sem dono."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    LearningAttemptModel.decision_id,
                    LearningAttemptModel.created_at,
                    LearningAttemptModel.status,
                    LearningAttemptModel.skipped,
                    LearningAttemptModel.hint_level,
                    LearningAttemptModel.reflection,
                    DecisionPointModel.match_id,
                    DecisionPointModel.participant_id,
                    ParticipantModel.win,
                )
                .join(DecisionPointModel, DecisionPointModel.id == LearningAttemptModel.decision_id)
                .outerjoin(
                    ParticipantModel,
                    (ParticipantModel.match_id == DecisionPointModel.match_id)
                    & (ParticipantModel.participant_id == DecisionPointModel.participant_id),
                )
                .where(LearningAttemptModel.user_id.is_not_distinct_from(owner))
            )
        ).all()
        facts = [
            AttemptFact(
                decision_id=row.decision_id,
                match_id=row.match_id,
                participant_id=row.participant_id,
                created_at=row.created_at,
                status=row.status,
                skipped=row.skipped,
                hint_level=row.hint_level,
                reflected=row.reflection is not None,
                lost=row.win is False,
            )
            for row in rows
        ]
        # Decisões analisáveis das partidas estudadas, para "Partida dominada". Uma decisão só
        # aparece numa extração; pontos de outras extrações da mesma partida têm outro id.
        keys = {(f.match_id, f.participant_id) for f in facts}
        eligible_by_match: dict[tuple[str, int], set[uuid.UUID]] = {key: set() for key in keys}
        if keys:
            studied_extractions = (
                (
                    await session.execute(
                        select(DecisionPointModel.extraction_id).where(
                            DecisionPointModel.id.in_({f.decision_id for f in facts})
                        )
                    )
                )
                .scalars()
                .all()
            )
            points = (
                await session.execute(
                    select(
                        DecisionPointModel.id,
                        DecisionPointModel.match_id,
                        DecisionPointModel.participant_id,
                    ).where(
                        DecisionPointModel.extraction_id.in_(set(studied_extractions)),
                        DecisionPointModel.eligible.is_(True),
                    )
                )
            ).all()
            for point in points:
                key = (point.match_id, point.participant_id)
                if key in eligible_by_match:
                    eligible_by_match[key].add(point.id)
    return summarize(
        facts,
        now=now or datetime.now(UTC),
        eligible_by_match=eligible_by_match,
        match_id=match_id,
    )
