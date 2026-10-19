"""Sentinela observacional por patch/fila; não confirma nem libera regras."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Integer,
    String,
    func,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.ingestion.final_items import FinalItems
from riftlab.ingestion.models import MatchModel, ParticipantFinalItemsModel, ParticipantModel
from riftlab.shared.db import Base, UuidPkMixin
from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.application import (
    RECONSTRUCTION_ALGORITHM_VERSION,
    _load_catalog_items_closure,
    _referenced_item_ids,
    reconstruct_participant,
)
from riftlab.timeline.closure import closure_detail, verdict
from riftlab.timeline.empirical_rules import EmpiricalRules, attribute_system_purchases
from riftlab.timeline.models import MatchReconstructionModel
from riftlab.timeline.repository import SqlAlchemyTimelineRepository


class PatchRuleHealthModel(UuidPkMixin, Base):
    __tablename__ = "patch_rule_health"
    __table_args__ = (
        CheckConstraint("application IN ('VALIDATED', 'INHERITED')", name="health_application"),
        CheckConstraint("queue IS NULL OR queue IN (420, 440)", name="health_queue"),
        CheckConstraint(
            "reconstruction_version > 0 AND clean_participants >= 0 AND failures >= 0 "
            "AND failures <= clean_participants AND contributing_matches >= 0 "
            "AND contributing_matches <= clean_participants",
            name="health_counts",
        ),
        CheckConstraint("verdict IN ('ACCEPT', 'REJECT', 'NO_SAMPLE')", name="health_verdict"),
        CheckConstraint(
            "NOT is_invalidation OR (verdict = 'NO_SAMPLE' AND clean_participants = 0 "
            "AND failures = 0 AND contributing_matches = 0 AND wilson_upper IS NULL)",
            name="health_invalidation_shape",
        ),
        CheckConstraint(
            "wilson_upper IS NULL OR (wilson_upper >= 0 AND wilson_upper <= 1)",
            name="health_wilson",
        ),
        CheckConstraint("length(rules_fingerprint) = 64", name="health_fingerprint"),
        CheckConstraint("length(source_patch) > 0", name="health_source_patch"),
    )
    game_patch: Mapped[str] = mapped_column(String, index=True)
    queue: Mapped[int | None] = mapped_column(Integer)
    application: Mapped[str] = mapped_column(String)
    source_patch: Mapped[str] = mapped_column(String)
    rules_fingerprint: Mapped[str] = mapped_column(String(64))
    reconstruction_version: Mapped[int] = mapped_column(Integer)
    clean_participants: Mapped[int] = mapped_column(Integer)
    failures: Mapped[int] = mapped_column(Integer)
    contributing_matches: Mapped[int] = mapped_column(Integer)
    wilson_upper: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[str] = mapped_column(String)
    is_invalidation: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


def fingerprint(provenance: list[dict[str, Any]]) -> str:
    applied = sorted(
        (
            r["ruleId"],
            r["ruleVersion"],
            r.get("application"),
            r.get("appliedFromPatch"),
            tuple(r.get("systemItemIds", ())) if r["ruleId"] == "R4s" else (),
            # Só proveniência de domínio (v12) carrega fila/contexto; a forma antiga
            # mantém o mesmo fingerprint.
            *(
                (r.get("appliedFromQueue"), r.get("catalogContext"))
                if "appliedFromQueue" in r
                else ()
            ),
        )
        for r in provenance
        if r.get("application") != "WITHHELD"
    )
    return hashlib.sha256(json.dumps(applied, separators=(",", ":")).encode()).hexdigest()


def public_health(
    row: PatchRuleHealthModel | None,
    inherited: bool,
    retained_reject: PatchRuleHealthModel | None = None,
) -> dict[str, Any]:
    status = "UNVERIFIED" if row is None or row.verdict == "NO_SAMPLE" else "OK"
    suspended = inherited and (
        row is not None and row.verdict == "REJECT" or retained_reject is not None
    )
    if suspended:
        status = "SUSPENDED"
    display_row = row if row is not None and row.verdict != "NO_SAMPLE" else retained_reject or row
    return {
        "status": status,
        "cleanParticipants": display_row.clean_participants if display_row else 0,
        "failures": display_row.failures if display_row else 0,
        "verdict": row.verdict if row else None,
        "wilsonUpper": row.wilson_upper if row else None,
    }


async def invalidate_patch_health(session: AsyncSession, patch: str) -> int:
    """Append NO_SAMPLE markers after source matches are removed; preserve prior reports."""
    dimensions = (
        PatchRuleHealthModel.application,
        PatchRuleHealthModel.source_patch,
        PatchRuleHealthModel.rules_fingerprint,
        PatchRuleHealthModel.reconstruction_version,
        PatchRuleHealthModel.queue,
    )
    rows = (
        await session.execute(
            select(*dimensions).where(PatchRuleHealthModel.game_patch == patch).distinct()
        )
    ).all()
    for application, source, rules_fp, version, queue in rows:
        session.add(
            PatchRuleHealthModel(
                game_patch=patch,
                queue=queue,
                application=application,
                source_patch=source,
                rules_fingerprint=rules_fp,
                reconstruction_version=version,
                clean_participants=0,
                failures=0,
                contributing_matches=0,
                wilson_upper=None,
                verdict="NO_SAMPLE",
                is_invalidation=True,
            )
        )
    return len(rows)


async def health_for_state(
    session: AsyncSession,
    patch: str,
    version: int,
    provenance: list[dict[str, Any]],
    queue: int | None = None,
) -> dict[str, Any]:
    """Saúde aplicável a um estado. Com `queue` (CC-804), vale a medição da própria fila:
    REJECT de uma fila suspende só ela, e um ACCEPT de outra população não esconde esse
    REJECT. Sem linha da fila (histórico antigo), vale o agregado do grupo."""
    inherited = any(r.get("application") == "INHERITED" for r in provenance)
    applied = [r for r in provenance if r.get("application") != "WITHHELD"]
    if not applied:
        return public_health(None, False)
    first = applied[0]
    application = first.get("application") or "VALIDATED"
    source = first.get("appliedFromPatch") or patch

    def query(dimension: int | None, *, measured_only: bool = False) -> Any:
        statement = select(PatchRuleHealthModel).where(
            PatchRuleHealthModel.game_patch == patch,
            PatchRuleHealthModel.queue.is_(None)
            if dimension is None
            else PatchRuleHealthModel.queue == dimension,
            PatchRuleHealthModel.application == application,
            PatchRuleHealthModel.source_patch == source,
            PatchRuleHealthModel.reconstruction_version == version,
            PatchRuleHealthModel.rules_fingerprint == fingerprint(provenance),
        )
        if measured_only:
            statement = statement.where(PatchRuleHealthModel.is_invalidation.is_(False))
        return statement.order_by(
            PatchRuleHealthModel.computed_at.desc(), PatchRuleHealthModel.id.desc()
        ).limit(1)

    dimension = queue
    row = (await session.scalars(query(queue))).first() if queue is not None else None
    if row is None:
        dimension = None
        row = (await session.scalars(query(None))).first()
    # Só lápides de invalidação retêm a medição vigente anterior. Um NO_SAMPLE
    # produzido por medição real é uma nova observação e encerra retenção.
    retained_reject = None
    if inherited and row is not None and row.is_invalidation:
        latest_measurement = await session.scalar(query(dimension, measured_only=True))
        if latest_measurement is not None and latest_measurement.verdict == "REJECT":
            retained_reject = latest_measurement
    return {**public_health(row, inherited, retained_reject), "queue": dimension}


async def compute_health(
    sf: async_sessionmaker[AsyncSession], rules: EmpiricalRules, patch: str
) -> list[dict[str, Any]]:
    from riftlab.catalog.repository import SqlAlchemyCatalogRepository
    from riftlab.dataset.rows import _catalog

    groups: dict[tuple[str, str, str, int | None], list[Any]] = defaultdict(lambda: [0, 0, set()])
    async with sf.begin() as session:
        # Serializa recálculos do mesmo patch antes de ler a amostra.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": "rule-health:" + patch}
        )
        matches = (await session.scalars(select(MatchModel).where(MatchModel.patch == patch))).all()
        for match in matches:
            marker = await session.get(
                MatchReconstructionModel, (match.match_id, RECONSTRUCTION_ALGORITHM_VERSION)
            )
            if marker is None or marker.catalog_id is None:
                continue
            provenance = marker.rules_provenance or []
            applied = [r for r in provenance if r.get("application") != "WITHHELD"]
            if not applied:
                continue
            enabled = frozenset(r["ruleId"] for r in applied)
            fp = fingerprint(provenance)
            application = applied[0]["application"]
            source = applied[0].get("appliedFromPatch") or patch
            system_ids = frozenset(
                next(
                    (r.get("systemItemIds", ()) for r in applied if r.get("ruleId") == "R4s"),
                    (),
                )
            )
            for queue in (None, 420, 440):
                groups[(application, source, fp, queue)]
            officials = (
                await session.scalars(
                    select(ParticipantFinalItemsModel).where(
                        ParticipantFinalItemsModel.match_id == match.match_id,
                        ParticipantFinalItemsModel.raw_hash == match.raw_hash,
                    )
                )
            ).all()
            if not officials:
                continue
            bundle = await _catalog(session, marker.catalog_id)
            full_catalog = {i.item_id: i for i in bundle.items}
            repo = SqlAlchemyCatalogRepository(session)
            events = await SqlAlchemyTimelineRepository(session).list_events(match.match_id)
            # O replay usa o mesmo contexto de catálogo da reconstrução (domínio FULL
            # confirmado no CC-802); a forma antiga é o fecho de receitas da v11.
            if any(r.get("catalogContext") == "FULL_CANONICAL_V1" for r in applied):
                catalog = full_catalog
            else:
                catalog = await _load_catalog_items_closure(
                    repo, marker.catalog_id, await _referenced_item_ids(events)
                )
            excluded = frozenset(
                i.item_id for i in await repo.list_excluded_items(marker.catalog_id)
            )
            if "R4w" in enabled:
                supports = (
                    await session.scalars(
                        select(ParticipantModel.participant_id).where(
                            ParticipantModel.match_id == match.match_id,
                            ParticipantModel.role == "UTILITY",
                        )
                    )
                ).all()
                events = attribute_system_purchases(events, supports)
            for official in officials:
                replay = reconstruct_participant(
                    participant_id=official.participant_id,
                    events=events,
                    origin=SourceOrigin.COMPLETED_MATCH,
                    catalog_id=marker.catalog_id,
                    catalog_items=catalog,
                    excluded_item_ids=excluded,
                    enabled_rules=enabled,
                    system_item_ids=system_ids if "R4s" in enabled else frozenset(),
                )
                if replay.uncertain:
                    continue
                final = FinalItems(
                    tuple(official.items), official.trinket_item_id, official.role_bound_item_id
                )
                failed = closure_detail(replay.final_inventory, final, full_catalog).failed
                for queue in (None, match.queue):
                    group = groups[(application, source, fp, queue)]
                    group[0] += 1
                    group[1] += int(failed)
                    group[2].add(match.match_id)
        reports = []
        for (application, source, fp, queue), (n, k, contributing) in groups.items():
            outcome, upper = verdict(n, k, len(contributing))
            row = PatchRuleHealthModel(
                game_patch=patch,
                queue=queue,
                application=application,
                source_patch=source,
                rules_fingerprint=fp,
                reconstruction_version=RECONSTRUCTION_ALGORITHM_VERSION,
                clean_participants=n,
                failures=k,
                contributing_matches=len(contributing),
                wilson_upper=upper,
                verdict=outcome,
            )
            session.add(row)
            reports.append(
                dict(
                    patch=patch,
                    queue=queue,
                    application=application,
                    sourcePatch=source,
                    cleanParticipants=n,
                    failures=k,
                    contributingMatches=len(contributing),
                    wilsonUpper=upper,
                    verdict=outcome,
                )
            )
    return reports
