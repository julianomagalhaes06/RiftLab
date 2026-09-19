"""Verificação de integridade da ingestão (CC-206): prova, no banco, que "nenhuma partida
parcial é analisável". Cada invariante violada é contada por código; zero em todos os
códigos é o estado esperado. Só agregados (nenhum matchId, hash ou jogador na saída).

Invariantes:
- MATCH_WITHOUT_PARTICIPANTS / MATCH_WITHOUT_EVENTS: partida persistida sem participantes
  ou sem linha do tempo (importação é tudo-ou-nada);
- ORPHAN_MATCH: partida sem importação concluída registrada;
- READY_WITHOUT_EXTRACTION: preparação SUCCEEDED (READY) sem reconstrução ou sem extração
  das versões correntes (READY só existe depois da extração);
- EXTRACTION_WITHOUT_READY_PREPARATION não é verificado (a extração manual por CLI é
  legítima); pontos sem extração são barrados por chave estrangeira;
- RAW_PAIR_MISSING (opcional, com o armazenamento local): partida REAL importada sem o
  par bruto íntegro em disco."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from pathlib import Path
from typing import Any

from sqlalchemy import exists, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.decisions.application import resolve_current_versions
from riftlab.decisions.repository import SqlAlchemyDecisionRepository
from riftlab.ingestion.application import raw_pair_available
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import ImportJobModel, MatchModel, ParticipantModel
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.reports.reconstruction import real_imported_match_ids
from riftlab.shared.db import read_only_unit_of_work
from riftlab.shared.errors import DomainError
from riftlab.timeline.models import TimelineEventModel

SessionFactory = async_sessionmaker[AsyncSession]

VIOLATION_CODES = (
    "MATCH_WITHOUT_PARTICIPANTS",
    "MATCH_WITHOUT_EVENTS",
    "ORPHAN_MATCH",
    "READY_WITHOUT_EXTRACTION",
    "RAW_PAIR_MISSING",
)


async def check_integrity(
    session_factory: SessionFactory,
    scope_path: Path,
    *,
    raw_store: DurableRawMatchStore | None = None,
    match_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    """`match_ids` restringe a verificação a um conjunto (ex.: a amostra coletada); sem
    ele confere o banco inteiro."""
    violations: Counter[str] = Counter({code: 0 for code in VIOLATION_CODES})
    scoped = MatchModel.match_id.in_(sorted(match_ids)) if match_ids is not None else true()

    async def count_matches(session: AsyncSession, *conditions: Any) -> int:
        query = select(func.count()).select_from(MatchModel).where(scoped, *conditions)
        return int(await session.scalar(query) or 0)

    async with read_only_unit_of_work(session_factory) as session:
        matches = await count_matches(session)
        violations["MATCH_WITHOUT_PARTICIPANTS"] = await count_matches(
            session, ~exists().where(ParticipantModel.match_id == MatchModel.match_id)
        )
        violations["MATCH_WITHOUT_EVENTS"] = await count_matches(
            session, ~exists().where(TimelineEventModel.match_id == MatchModel.match_id)
        )
        violations["ORPHAN_MATCH"] = await count_matches(
            session,
            ~exists().where(
                ImportJobModel.match_id == MatchModel.match_id,
                ImportJobModel.kind == "IMPORT",
                ImportJobModel.state == "SUCCEEDED",
            ),
        )

        ingestion = SqlAlchemyIngestionRepository(session)
        decisions = SqlAlchemyDecisionRepository(session)
        all_ids = list(await session.scalars(select(MatchModel.match_id).where(scoped)))
        jobs = await ingestion.list_latest_preparation_jobs(all_ids)
        ready = [m for m, job in jobs.items() if job.state.value == "SUCCEEDED"]
        for match_id in ready:
            try:
                current = await resolve_current_versions(session, match_id, scope_path)
            except DomainError:
                violations["READY_WITHOUT_EXTRACTION"] += 1
                continue
            if await decisions.get_extraction(match_id, current.versions) is None:
                violations["READY_WITHOUT_EXTRACTION"] += 1

        real_ids = await real_imported_match_ids(session, match_ids)
    if raw_store is not None:
        violations["RAW_PAIR_MISSING"] = sum(
            1 for match_id in real_ids if not raw_pair_available(raw_store, match_id)
        )
    total = sum(violations.values())
    return {
        "matchesChecked": matches,
        "readyChecked": len(ready),
        "realMatchesChecked": len(real_ids),
        "rawStoreChecked": raw_store is not None,
        "violations": dict(violations),
        "violationsTotal": total,
        "ok": total == 0,
    }


def render_integrity_report(data: dict[str, Any]) -> str:
    lines = [
        f"integridade: {data['matchesChecked']} partidas, {data['readyChecked']} READY, "
        f"{data['realMatchesChecked']} reais (raw conferido: {data['rawStoreChecked']})",
    ]
    lines += [f"  {code}: {count}" for code, count in data["violations"].items()]
    lines.append(
        "resultado: OK (nenhuma partida parcial analisável)"
        if data["ok"]
        else "resultado: VIOLAÇÕES"
    )
    return "\n".join(lines)
