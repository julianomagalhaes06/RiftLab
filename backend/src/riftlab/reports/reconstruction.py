"""Relatório de reconstrução e decisões da amostra REAL (CC-206). Reaproveita o código
de produto do CC-204 (`resolve_current_versions`, repositório de decisões, estado
público da preparação): não há cálculo paralelo de versões, elegibilidade ou motivos.

Escopo: partidas importadas em modo RIOT com importação concluída. Saída agregada:
estados da preparação, estados reconstruídos e a fração com pré-decisão incerta, pontos
de decisão, elegíveis, motivos e sub-motivos de inelegibilidade, por patch. Nenhum
matchId, hash ou jogador."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.decisions.application import resolve_current_versions
from riftlab.decisions.repository import SqlAlchemyDecisionRepository
from riftlab.ingestion.domain import ImportMode
from riftlab.ingestion.models import ImportJobModel
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.preparation.application import preparation_info
from riftlab.shared.db import read_only_unit_of_work
from riftlab.shared.errors import DomainError
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
from riftlab.timeline.models import DecisionStateModel

SessionFactory = async_sessionmaker[AsyncSession]


def _pct(part: int, whole: int) -> float | None:
    """None quando não há base (nunca 0% de nada)."""
    return round(100.0 * part / whole, 2) if whole else None


# Sub-motivos de OUT_OF_SCOPE que dizem "esta decisão é de FORA do recorte congelado"
# (fila, mapa, patch, função ou campeão). O ordinal do item principal NÃO entra: é uma
# restrição de seleção, não de recorte.
RECORTE_SUB_REASONS = frozenset(
    {
        "QUEUE_OUT_OF_SCOPE",
        "MAP_OUT_OF_SCOPE",
        "PATCH_OUT_OF_SCOPE",
        "ROLE_UNKNOWN",
        "ROLE_OUT_OF_SCOPE",
        "CHAMPION_OUT_OF_SCOPE",
    }
)


@dataclass(slots=True)
class PatchStats:
    matches: int = 0
    points: int = 0
    eligible: int = 0


@dataclass(slots=True)
class ReconstructionReport:
    matches_imported: int = 0
    preparation: Counter[str] = field(default_factory=Counter)
    preparation_errors: Counter[str] = field(default_factory=Counter)
    matches_reconstructed: int = 0
    matches_extracted: int = 0
    matches_with_catalog: int = 0
    matches_without_catalog: int = 0
    states_total: int = 0
    states_pre_decision_uncertain: int = 0
    points_total: int = 0
    points_eligible: int = 0
    reasons: Counter[str] = field(default_factory=Counter)
    sub_reasons: Counter[str] = field(default_factory=Counter)
    # Só os pontos DENTRO do recorte (fila, mapa, patch, função e campeão do scope.yaml): é
    # o que importa para o gate, já que a extração cobre os 10 participantes de cada partida.
    recorte_points: int = 0
    recorte_eligible: int = 0
    recorte_reasons: Counter[str] = field(default_factory=Counter)
    recorte_sub_reasons: Counter[str] = field(default_factory=Counter)
    by_patch: dict[str, PatchStats] = field(default_factory=dict)
    reconstruction_version: int = RECONSTRUCTION_ALGORITHM_VERSION

    def as_dict(self) -> dict[str, Any]:
        ineligible = self.points_total - self.points_eligible
        return {
            "reconstructionVersion": self.reconstruction_version,
            "matchesImported": self.matches_imported,
            "preparation": dict(sorted(self.preparation.items())),
            "preparationErrors": dict(sorted(self.preparation_errors.items())),
            "matchesReconstructed": self.matches_reconstructed,
            "matchesExtracted": self.matches_extracted,
            "matchesWithCatalog": self.matches_with_catalog,
            "matchesWithoutCatalog": self.matches_without_catalog,
            "states": {
                "total": self.states_total,
                "preDecisionUncertain": self.states_pre_decision_uncertain,
                "preDecisionUncertainPct": _pct(
                    self.states_pre_decision_uncertain, self.states_total
                ),
            },
            "points": {
                "total": self.points_total,
                "eligible": self.points_eligible,
                "ineligible": ineligible,
                "eligiblePct": _pct(self.points_eligible, self.points_total),
                "discardedPct": _pct(ineligible, self.points_total),
            },
            "reasons": dict(sorted(self.reasons.items())),
            "subReasons": dict(sorted(self.sub_reasons.items())),
            "inRecorte": {
                "points": self.recorte_points,
                "eligible": self.recorte_eligible,
                "eligiblePct": _pct(self.recorte_eligible, self.recorte_points),
                "reasons": dict(sorted(self.recorte_reasons.items())),
                "subReasons": dict(sorted(self.recorte_sub_reasons.items())),
            },
            "byPatch": {
                patch: {
                    "matches": stats.matches,
                    "points": stats.points,
                    "eligible": stats.eligible,
                    "eligiblePct": _pct(stats.eligible, stats.points),
                }
                for patch, stats in sorted(self.by_patch.items())
            },
        }


async def real_imported_match_ids(
    session: AsyncSession, only: Collection[str] | None = None
) -> list[str]:
    """Partidas com importação RIOT concluída (a amostra real; nunca as fixtures demo).
    `only` restringe a um conjunto (ex.: as partidas coletadas por um manifesto)."""
    query = (
        select(ImportJobModel.match_id)
        .where(
            ImportJobModel.kind == "IMPORT",
            ImportJobModel.mode == ImportMode.RIOT.value,
            ImportJobModel.state == "SUCCEEDED",
            ImportJobModel.match_id.is_not(None),
        )
        .distinct()
        .order_by(ImportJobModel.match_id)
    )
    if only is not None:
        query = query.where(ImportJobModel.match_id.in_(sorted(only)))
    return [match_id for match_id in await session.scalars(query) if match_id]


async def build_reconstruction_report(
    session_factory: SessionFactory, scope_path: Path, *, match_ids: Collection[str] | None = None
) -> ReconstructionReport:
    report = ReconstructionReport()
    async with read_only_unit_of_work(session_factory) as session:
        ingestion = SqlAlchemyIngestionRepository(session)
        decisions = SqlAlchemyDecisionRepository(session)
        sample = await real_imported_match_ids(session, match_ids)
        report.matches_imported = len(sample)
        jobs = await ingestion.list_latest_preparation_jobs(sample)

        for match_id in sample:
            info = preparation_info(jobs.get(match_id))
            report.preparation[info.status.value] += 1
            if info.error_code:
                report.preparation_errors[info.error_code] += 1
            try:
                current = await resolve_current_versions(session, match_id, scope_path)
            except DomainError:  # sem reconstrução na versão corrente (ou fora do escopo)
                continue
            report.matches_reconstructed += 1
            patch = current.match.patch
            stats = report.by_patch.setdefault(patch, PatchStats())
            stats.matches += 1
            if current.catalog is None:
                report.matches_without_catalog += 1
            else:
                report.matches_with_catalog += 1

            total, uncertain = await _state_counts(session, match_id)
            report.states_total += total
            report.states_pre_decision_uncertain += uncertain

            marker = await decisions.get_extraction(match_id, current.versions)
            if marker is None:
                continue
            report.matches_extracted += 1
            for point in await decisions.list_points(marker.id):
                report.points_total += 1
                stats.points += 1
                if point.eligible:
                    report.points_eligible += 1
                    stats.eligible += 1
                subs = {
                    sub
                    for reason in point.reasons
                    if reason["code"] == "OUT_OF_SCOPE"
                    for sub in reason.get("subReasons", [])
                }
                in_recorte = not (subs & RECORTE_SUB_REASONS)
                if in_recorte:
                    report.recorte_points += 1
                    report.recorte_eligible += int(point.eligible)
                for reason in point.reasons:
                    report.reasons[reason["code"]] += 1
                    if in_recorte:
                        report.recorte_reasons[reason["code"]] += 1
                    for sub in reason.get("subReasons", []):
                        report.sub_reasons[f"{reason['code']}:{sub}"] += 1
                        if in_recorte:
                            report.recorte_sub_reasons[f"{reason['code']}:{sub}"] += 1
    return report


async def _state_counts(session: AsyncSession, match_id: str) -> tuple[int, int]:
    uncertain = DecisionStateModel.quality["preDecisionQuality"]["uncertain"].as_boolean()
    row = (
        await session.execute(
            select(func.count(), func.count().filter(uncertain.is_(True))).where(
                DecisionStateModel.match_id == match_id,
                DecisionStateModel.state_version == RECONSTRUCTION_ALGORITHM_VERSION,
            )
        )
    ).one()
    return int(row[0]), int(row[1])


def render_reconstruction_report(data: dict[str, Any]) -> str:
    states, points = data["states"], data["points"]
    lines = [
        f"reconstrução v{data['reconstructionVersion']} — amostra REAL importada",
        f"partidas importadas: {data['matchesImported']}",
        f"preparação: {data['preparation'] or '-'}",
        f"falhas de preparação por código: {data['preparationErrors'] or '-'}",
        (
            f"reconstruídas: {data['matchesReconstructed']} | extraídas: "
            f"{data['matchesExtracted']} | com catálogo: {data['matchesWithCatalog']} | "
            f"sem catálogo: {data['matchesWithoutCatalog']}"
        ),
        (
            f"estados: {states['total']} | pré-decisão incerta: "
            f"{states['preDecisionUncertain']} ({_fmt(states['preDecisionUncertainPct'])})"
        ),
        (
            f"pontos de decisão: {points['total']} | elegíveis: {points['eligible']} "
            f"({_fmt(points['eligiblePct'])}) | inelegíveis: {points['ineligible']} "
            f"({_fmt(points['discardedPct'])})"
        ),
        f"motivos: {data['reasons'] or '-'}",
        f"sub-motivos: {data['subReasons'] or '-'}",
        (
            f"DENTRO DO RECORTE: {data['inRecorte']['points']} pontos | elegíveis: "
            f"{data['inRecorte']['eligible']} ({_fmt(data['inRecorte']['eligiblePct'])})"
        ),
        f"  motivos (no recorte): {data['inRecorte']['reasons'] or '-'}",
        f"  sub-motivos (no recorte): {data['inRecorte']['subReasons'] or '-'}",
    ]
    for patch, stats in data["byPatch"].items():
        lines.append(
            f"patch {patch}: {stats['matches']} partidas, {stats['points']} pontos, "
            f"{stats['eligible']} elegíveis ({_fmt(stats['eligiblePct'])})"
        )
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "n/d" if value is None else f"{value}%"
