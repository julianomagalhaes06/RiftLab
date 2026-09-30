"""Triagem agregada para um NOVO pré-registro; não executa confirmação."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.patches import current_patch_window
from riftlab.decisions.scope import load_real_scope
from riftlab.ingestion.models import MatchModel, ParticipantModel
from riftlab.reports.reconstruction import real_imported_match_ids
from riftlab.shared.db import read_only_unit_of_work

SessionFactory = async_sessionmaker[AsyncSession]

# Protocolado no CC-207/00-pre-registro.md; nenhum limiar deste card é alterado.
PROTOCOL = {
    "H1": (200, 5),
    "H1b": (100, 5),
    "H2": (30, 5),
    "H2b": (50, 5),
    "H3": (30, 5),
    "H4": (30, 5),
    "H5": (200, 5),
    "H6": (60, 5),
    "G-FINAL": (20, 3),
}


async def patch_readiness(session_factory: SessionFactory, scope_path: Path) -> dict[str, object]:
    """Limites superiores de amostra. `POSSIBLE` nunca significa gate aprovado:
    unidades específicas/limpeza exigem novo protocolo e auditoria autorizada."""
    async with read_only_unit_of_work(session_factory) as session:
        window = await current_patch_window(session)
        scope = load_real_scope(scope_path, window)
        imported = await real_imported_match_ids(session)
        rows = (
            await session.execute(
                select(
                    MatchModel.patch,
                    func.count(func.distinct(MatchModel.match_id)),
                    func.count(ParticipantModel.participant_id),
                )
                .join(ParticipantModel, MatchModel.match_id == ParticipantModel.match_id)
                .where(MatchModel.match_id.in_(imported))
                .group_by(MatchModel.patch)
            )
        ).all()
    observed = {patch: (matches, participants) for patch, matches, participants in rows}
    by_patch = {}
    for patch in sorted(set(scope.patches) | set(observed)):
        matches, participants = observed.get(patch, (0, 0))
        # Cada hipótese tem unidade distinta. 10 participantes por partida é apenas
        # limite superior para gates por participante; eventos não são contados aqui.
        by_patch[patch] = {
            "matches": matches,
            "participantsUpperBound": participants,
            "inCurrentWindow": patch in scope.patches,
            "minimums": {
                name: {
                    "nMin": n_min,
                    "mMin": m_min,
                    "status": "INSUFFICIENT"
                    if matches < m_min
                    or (name in {"H1b", "H2b", "G-FINAL"} and participants < n_min)
                    else "NEEDS_UNIT_AUDIT",
                }
                for name, (n_min, m_min) in PROTOCOL.items()
            },
        }
    return {
        "protocol": "CC-207/00-pre-registro.md",
        "window": sorted(scope.patches),
        "scopeVersion": scope.version,
        "patches": by_patch,
        "confirmationRun": False,
    }
