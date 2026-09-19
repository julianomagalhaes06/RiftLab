"""Diversidade e concentração da amostra REAL (CC-206): SÓ agregados.

"Por jogador pseudonimizado" significa contar, por `subject_hash`, em quantas partidas
da amostra cada jogador aparece e resumir a DISTRIBUIÇÃO (histograma, máximos, fração
de partidas do jogador mais frequente). O hash nunca sai daqui: a saída contém números,
IDs de campeão e patches, jamais um hash, um ID de jogador ou um matchId. Amostra grande
não é amostra representativa: os números descrevem a concentração, não a
representatividade."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.decisions.scope import load_real_scope
from riftlab.ingestion.models import MatchModel, ParticipantModel
from riftlab.reports.reconstruction import real_imported_match_ids
from riftlab.shared.db import read_only_unit_of_work

SessionFactory = async_sessionmaker[AsyncSession]
_BUCKETS = ((1, 1, "1"), (2, 2, "2"), (3, 5, "3-5"), (6, 10, "6-10"), (11, None, "11+"))


def _pct(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 2) if whole else None


def _histogram(counts: list[int]) -> dict[str, int]:
    result = {label: 0 for _, _, label in _BUCKETS}
    for value in counts:
        for low, high, label in _BUCKETS:
            if value >= low and (high is None or value <= high):
                result[label] += 1
                break
    return result


async def build_diversity_report(
    session_factory: SessionFactory, scope_path: Path, *, match_ids: Collection[str] | None = None
) -> dict[str, Any]:
    scope = load_real_scope(scope_path)
    async with read_only_unit_of_work(session_factory) as session:
        sample = await real_imported_match_ids(session, match_ids)
        if not sample:
            return _empty()
        match_ids = sample
        per_player = [
            int(count)
            for count in await session.scalars(
                select(func.count(func.distinct(ParticipantModel.match_id)))
                .where(ParticipantModel.match_id.in_(match_ids))
                .group_by(ParticipantModel.subject_hash)
            )
        ]
        patch_rows = (
            await session.execute(
                select(MatchModel.patch, func.count())
                .where(MatchModel.match_id.in_(match_ids))
                .group_by(MatchModel.patch)
            )
        ).all()
        in_scope_matches = int(
            await session.scalar(
                select(func.count(func.distinct(ParticipantModel.match_id)))
                .join(MatchModel, MatchModel.match_id == ParticipantModel.match_id)
                .where(
                    ParticipantModel.match_id.in_(match_ids),
                    MatchModel.patch.in_(sorted(scope.patches)),
                    ParticipantModel.role == scope.role,
                    ParticipantModel.champion_id.in_(sorted(scope.champion_ids)),
                )
            )
            or 0
        )
        champion_rows = (
            await session.execute(
                select(
                    MatchModel.patch,
                    ParticipantModel.champion_id,
                    ParticipantModel.role,
                    func.count(),
                )
                .join(MatchModel, MatchModel.match_id == ParticipantModel.match_id)
                .where(ParticipantModel.match_id.in_(match_ids))
                .group_by(MatchModel.patch, ParticipantModel.champion_id, ParticipantModel.role)
            )
        ).all()

    matches = len(match_ids)
    participations = sum(per_player)
    ordered = sorted(per_player, reverse=True)
    champions: Counter[int] = Counter()
    in_scope: dict[str, Counter[int]] = {patch: Counter() for patch in sorted(scope.patches)}
    for patch, champion_id, role, count in champion_rows:
        champions[int(champion_id)] += int(count)
        if patch in scope.patches and role == scope.role and champion_id in scope.champion_ids:
            in_scope[patch][int(champion_id)] += int(count)
    return {
        "matches": matches,
        "participations": participations,
        "distinctPlayers": len(per_player),
        "matchesPerPlayer": {
            "histogram": _histogram(per_player),
            "max": ordered[0] if ordered else 0,
            "playersInAtLeast5Matches": sum(1 for value in per_player if value >= 5),
        },
        "concentration": {
            # fração das partidas em que aparece o jogador mais frequente
            "top1PlayerMatchShare": _pct(ordered[0], matches) if ordered else None,
            # fração das participações (vagas de jogador) ocupadas pelos 5 mais frequentes
            "top5PlayersParticipationShare": _pct(sum(ordered[:5]), participations),
        },
        "patches": {patch: int(count) for patch, count in sorted(patch_rows)},
        "patchesInScope": sorted(scope.patches),
        "championCoverage": {str(cid): count for cid, count in sorted(champions.items())},
        "scope": {
            "role": scope.role,
            "championIds": sorted(scope.champion_ids),
            "participantsByPatchAndChampion": {
                patch: {str(cid): count for cid, count in sorted(counter.items())}
                for patch, counter in in_scope.items()
            },
            "matchesWithAnyInScopeParticipant": in_scope_matches,
        },
    }


def _empty() -> dict[str, Any]:
    return {
        "matches": 0,
        "participations": 0,
        "distinctPlayers": 0,
        "matchesPerPlayer": {"histogram": _histogram([]), "max": 0, "playersInAtLeast5Matches": 0},
        "concentration": {"top1PlayerMatchShare": None, "top5PlayersParticipationShare": None},
        "patches": {},
        "patchesInScope": [],
        "championCoverage": {},
        "scope": {
            "role": None,
            "championIds": [],
            "participantsByPatchAndChampion": {},
            "matchesWithAnyInScopeParticipant": None,
        },
    }


def render_diversity_report(data: dict[str, Any]) -> str:
    if not data["matches"]:
        return "diversidade: nenhuma partida REAL importada."
    lines = [
        f"partidas: {data['matches']} | participações: {data['participations']} | "
        f"jogadores distintos (pseudonimizados): {data['distinctPlayers']}",
        f"partidas por jogador: {data['matchesPerPlayer']['histogram']} | máximo: "
        f"{data['matchesPerPlayer']['max']} | jogadores em ≥5 partidas: "
        f"{data['matchesPerPlayer']['playersInAtLeast5Matches']}",
        f"concentração: jogador mais frequente em {data['concentration']['top1PlayerMatchShare']}% "
        f"das partidas; 5 mais frequentes ocupam "
        f"{data['concentration']['top5PlayersParticipationShare']}% das vagas",
        f"patches: {data['patches']} (no recorte: {data['patchesInScope']})",
        f"recorte (função {data['scope']['role']}, campeões {data['scope']['championIds']}): "
        f"{data['scope']['participantsByPatchAndChampion']}",
    ]
    return "\n".join(lines)
