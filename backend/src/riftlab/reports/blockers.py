"""Censo agregado dos bloqueios da extração corrente, sem efeitos de preparação."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.models import ItemModel
from riftlab.catalog.patches import current_patch_window
from riftlab.decisions.application import resolve_current_versions
from riftlab.decisions.models import DecisionPointModel
from riftlab.decisions.repository import SqlAlchemyDecisionRepository
from riftlab.decisions.scope import load_real_scope
from riftlab.ingestion.models import MatchModel
from riftlab.reports.patch_readiness import patch_readiness
from riftlab.reports.reconstruction import real_imported_match_ids
from riftlab.shared.db import read_only_unit_of_work
from riftlab.shared.errors import DomainError
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
from riftlab.timeline.health import PatchRuleHealthModel
from riftlab.timeline.models import DecisionStateModel

SessionFactory = async_sessionmaker[AsyncSession]
NO_PRE_DECISION_REASON = "NO_PRE_DECISION_REASON"
BLOCKER_REPORT_OUT_OF_SCOPE = frozenset(
    {
        "QUEUE_OUT_OF_SCOPE",
        "MAP_OUT_OF_SCOPE",
        "PATCH_OUT_OF_SCOPE",
        "ROLE_UNKNOWN",
        "ROLE_OUT_OF_SCOPE",
        "CHAMPION_OUT_OF_SCOPE",
        "MAIN_ITEM_ORDINAL_BEYOND_SECOND",
    }
)


@dataclass(frozen=True, slots=True)
class BlockerDecision:
    patch: str
    queue: int
    eligible: bool
    in_scope: bool
    catalog_id: UUID | None
    reasons: tuple[tuple[str, int | None], ...]


def _pct(numerator: int, denominator: int) -> float | None:
    return round(100.0 * numerator / denominator, 2) if denominator else None


def _pre_decision_reasons(quality: object) -> tuple[tuple[str, int | None], ...]:
    if not isinstance(quality, dict):
        return ()
    pre = quality.get("preDecisionQuality")
    raw = pre.get("reasons") if isinstance(pre, dict) else None
    if not isinstance(raw, list):
        return ()
    reasons: list[tuple[str, int | None]] = []
    for reason in raw:
        if not isinstance(reason, dict):
            continue
        code = reason.get("code")
        if not isinstance(code, str) or not code:
            continue
        item_id = reason.get("itemId")
        if isinstance(item_id, bool) or not isinstance(item_id, int):
            item_id = None
        reasons.append((code, item_id))
    return tuple(reasons)


def _in_scope(reasons: list[dict[str, Any]]) -> bool:
    return not any(
        sub_reason in BLOCKER_REPORT_OUT_OF_SCOPE
        for reason in reasons
        if isinstance(reason, dict) and reason.get("code") == "OUT_OF_SCOPE"
        for sub_reason in reason.get("subReasons", [])
        if isinstance(sub_reason, str)
    )


def _aggregate(
    decisions: Iterable[BlockerDecision],
    *,
    item_names: dict[tuple[UUID, int], str],
    filter_in_scope: bool,
    expected_contexts: set[tuple[str, int]],
    extraction_counts: Counter[tuple[str, int]],
    imported_counts: Counter[tuple[str, int]],
    missing_version_counts: Counter[tuple[str, int]],
    missing_extraction_counts: Counter[tuple[str, int]],
) -> dict[str, Any]:
    source_decisions = list(decisions)
    selected = [row for row in source_decisions if row.in_scope or not filter_in_scope]
    blocked = [row for row in selected if not row.eligible]
    first: Counter[tuple[str, int | None, str | None]] = Counter()
    cumulative: Counter[str] = Counter()
    by_patch_queue: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    for row in selected:
        stats = by_patch_queue[(row.patch, row.queue)]
        stats["decisions"] += 1
        stats["eligible"] += int(row.eligible)
        stats["ineligible"] += int(not row.eligible)
        stats["in_scope"] += int(row.in_scope)
        present_codes = {code for code, _item_id in row.reasons}
        for code in present_codes:
            cumulative[code] += 1
        if not row.eligible:
            if row.reasons:
                code, item_id = row.reasons[0]
                name = (
                    item_names.get((row.catalog_id, item_id))
                    if row.catalog_id is not None and item_id is not None
                    else None
                )
                first[(code, item_id, name)] += 1
            else:
                first[(NO_PRE_DECISION_REASON, None, None)] += 1

    source_total = len(source_decisions)
    total = len(selected)
    eligible = sum(row.eligible for row in selected)
    in_scope_count = sum(row.in_scope for row in source_decisions)
    first_rows = [
        {
            "code": code,
            "itemId": item_id,
            "itemName": name,
            "decisions": count,
            "denominator": len(blocked),
            "pctOfIneligible": _pct(count, len(blocked)),
        }
        for (code, item_id, name), count in sorted(
            first.items(), key=lambda item: (item[0][0], item[0][1] or -1, item[0][2] or "")
        )
    ]
    cumulative_rows = [
        {
            "code": code,
            "decisions": count,
            "denominator": total,
            "pctOfDecisions": _pct(count, total),
        }
        for code, count in sorted(cumulative.items())
    ]
    grouped: dict[str, dict[str, Any]] = {}
    for patch, queue in expected_contexts:
        by_patch_queue.setdefault((patch, queue), Counter())
    for (patch, queue), stats in sorted(by_patch_queue.items()):
        key = f"{patch}/{queue}"
        current_extractions = extraction_counts[(patch, queue)]
        grouped[key] = {
            "patch": patch,
            "queueId": queue,
            "importedMatches": imported_counts[(patch, queue)],
            "matchesWithoutCurrentVersions": missing_version_counts[(patch, queue)],
            "matchesWithoutCurrentExtraction": missing_extraction_counts[(patch, queue)],
            "decisions": stats["decisions"],
            "eligible": stats["eligible"],
            "ineligible": stats["ineligible"],
            "inScope": stats["in_scope"],
            "currentExtractionMatches": current_extractions,
            "measurementStatus": "MEASURED" if current_extractions else "NOT_MEASURED",
            "eligiblePct": _pct(stats["eligible"], stats["decisions"]),
            "ineligiblePct": _pct(stats["ineligible"], stats["decisions"]),
        }
    return {
        "totals": {
            "decisions": total,
            "sourceDecisions": source_total,
            "eligible": eligible,
            "ineligible": total - eligible,
            "inScope": in_scope_count,
            "outOfScope": source_total - in_scope_count,
            "eligiblePct": _pct(eligible, total),
            "ineligiblePct": _pct(total - eligible, total),
            "inScopePct": _pct(in_scope_count, source_total),
        },
        "byPatchQueue": grouped,
        "firstReasonByItem": first_rows,
        "cumulativeByCode": cumulative_rows,
    }


async def build_blockers_report(
    session_factory: SessionFactory,
    scope_path: Path,
    *,
    patch: str | None = None,
    queue: int | None = None,
    in_scope_only: bool = False,
) -> dict[str, Any]:
    """Select current extraction markers and aggregate blockers, using READ ONLY SQL only."""
    decisions: list[BlockerDecision] = []
    contexts: Counter[tuple[str, int, int, str, str, str, str, str | None, str | None]] = Counter()
    extraction_counts: Counter[tuple[str, int]] = Counter()
    missing_current_extraction = 0
    missing_current_versions = 0
    item_keys: set[tuple[UUID, int]] = set()
    selected_patches: set[str] = set()
    observed_queues: set[int] = set()
    imported_counts: Counter[tuple[str, int]] = Counter()
    missing_version_counts: Counter[tuple[str, int]] = Counter()
    missing_extraction_counts: Counter[tuple[str, int]] = Counter()
    version_failure_codes: Counter[str] = Counter()

    async with read_only_unit_of_work(session_factory) as session:
        adjacent = await current_patch_window(session)
        scope = load_real_scope(scope_path, adjacent)
        match_ids = await real_imported_match_ids(session)
        imported_matches = await session.execute(
            select(MatchModel.match_id, MatchModel.patch, MatchModel.queue)
            .where(MatchModel.match_id.in_(match_ids))
            .order_by(MatchModel.match_id)
        )
        repository = SqlAlchemyDecisionRepository(session)
        for match_id, match_patch, match_queue in imported_matches.all():
            if patch is not None and match_patch != patch:
                continue
            if queue is not None and match_queue != queue:
                continue
            selected_patches.add(match_patch)
            observed_queues.add(match_queue)
            imported_counts[(match_patch, match_queue)] += 1
            try:
                current = await resolve_current_versions(session, match_id, scope_path)
            except DomainError as exc:
                missing_current_versions += 1
                missing_version_counts[(match_patch, match_queue)] += 1
                version_failure_codes[getattr(exc, "code", type(exc).__name__)] += 1
                continue
            if current.match.patch != match_patch or current.match.queue != match_queue:
                missing_current_versions += 1
                missing_version_counts[(match_patch, match_queue)] += 1
                version_failure_codes["MATCH_METADATA_DIVERGED"] += 1
                continue
            marker = await repository.get_extraction(match_id, current.versions)
            if marker is None:
                missing_current_extraction += 1
                missing_extraction_counts[(match_patch, match_queue)] += 1
                continue
            context_key = (
                current.match.patch,
                current.match.queue,
                marker.versions.reconstruction_version,
                marker.versions.selection_version,
                marker.versions.feature_schema_version,
                marker.versions.scope_version,
                marker.versions.classification_version or "",
                marker.versions.curation_rules_version,
                current.catalog.data_dragon_version if current.catalog else None,
            )
            contexts[context_key] += 1
            extraction_counts[(current.match.patch, current.match.queue)] += 1
            result = await session.execute(
                select(DecisionPointModel, DecisionStateModel.quality)
                .join(
                    DecisionStateModel,
                    DecisionStateModel.id == DecisionPointModel.decision_state_id,
                )
                .where(DecisionPointModel.extraction_id == marker.id)
            )
            for point, quality in result.all():
                reasons = _pre_decision_reasons(quality)
                for _code, item_id in reasons:
                    if item_id is not None and marker.versions.catalog_id is not None:
                        item_keys.add((marker.versions.catalog_id, item_id))
                decisions.append(
                    BlockerDecision(
                        patch=current.match.patch,
                        queue=current.match.queue,
                        eligible=point.eligible,
                        in_scope=_in_scope(point.reasons),
                        catalog_id=marker.versions.catalog_id,
                        reasons=reasons,
                    )
                )

        item_names: dict[tuple[UUID, int], str] = {}
        by_catalog: dict[UUID, set[int]] = defaultdict(set)
        for catalog_id, item_id in item_keys:
            by_catalog[catalog_id].add(item_id)
        for catalog_id, item_ids in by_catalog.items():
            names = await session.execute(
                select(ItemModel.item_id, ItemModel.attributes["name"].astext).where(
                    ItemModel.catalog_id == catalog_id,
                    ItemModel.item_id.in_(item_ids),
                )
            )
            for item_id, name in names.all():
                if isinstance(name, str) and name:
                    item_names[(catalog_id, item_id)] = name

        expected_patches = selected_patches or ({patch} if patch else set(scope.patches))
        expected_queues = {queue} if queue is not None else (set(scope.queues) | observed_queues)
        health_rows = (
            await session.scalars(
                select(PatchRuleHealthModel)
                .where(PatchRuleHealthModel.game_patch.in_(expected_patches))
                .order_by(
                    PatchRuleHealthModel.game_patch,
                    PatchRuleHealthModel.queue,
                    PatchRuleHealthModel.computed_at,
                )
            )
        ).all()
        health_by_context = [
            {
                "patch": row.game_patch,
                "queueId": row.queue,
                "application": row.application,
                "sourcePatch": row.source_patch,
                "rulesFingerprint": row.rules_fingerprint,
                "reconstructionVersion": row.reconstruction_version,
                "cleanParticipants": row.clean_participants,
                "failures": row.failures,
                "contributingMatches": row.contributing_matches,
                "wilsonUpper": row.wilson_upper,
                "verdict": row.verdict,
                "isInvalidation": row.is_invalidation,
                "computedAt": row.computed_at.isoformat() if row.computed_at else None,
            }
            for row in health_rows
        ]

    # Reuses the report's own READ ONLY transaction contract. This computes only
    # aggregate sample bounds from imported participants; it never computes health.
    readiness = await patch_readiness(session_factory, scope_path)
    readiness_by_patch = cast(dict[str, dict[str, Any]], readiness["patches"])
    versions = sorted(
        (
            {
                "patch": key[0],
                "queueId": key[1],
                "reconstructionVersion": key[2],
                "selectionVersion": key[3],
                "featureSchemaVersion": key[4],
                "scopeVersion": key[5],
                "classificationVersion": key[6] or None,
                "curationRulesVersion": key[7],
                "dataDragonVersion": key[8],
                "currentExtractionMatches": count,
            }
            for key, count in contexts.items()
        ),
        key=lambda item: (
            item["patch"],
            item["queueId"],
            item["reconstructionVersion"],
            item["selectionVersion"],
        ),
    )
    expected_contexts = {
        (expected_patch, expected_queue)
        for expected_patch in expected_patches
        for expected_queue in expected_queues
    }
    aggregated = _aggregate(
        decisions,
        item_names=item_names,
        filter_in_scope=in_scope_only,
        expected_contexts=expected_contexts,
        extraction_counts=extraction_counts,
        imported_counts=imported_counts,
        missing_version_counts=missing_version_counts,
        missing_extraction_counts=missing_extraction_counts,
    )
    return {
        "report": "CC-802 blockers census",
        "measured": bool(extraction_counts),
        "filters": {"patch": patch, "queueId": queue, "inScopeOnly": in_scope_only},
        "scopeVersion": scope.version,
        "reconstructionVersion": RECONSTRUCTION_ALGORITHM_VERSION,
        "missingCurrentExtractionMatches": missing_current_extraction,
        "matchesWithoutCurrentVersions": missing_current_versions,
        "currentVersionFailureCodes": dict(sorted(version_failure_codes.items())),
        "importedMatches": sum(imported_counts.values()),
        "versions": versions,
        "totals": aggregated["totals"],
        "byPatchQueue": aggregated["byPatchQueue"],
        "firstReasonByItem": aggregated["firstReasonByItem"],
        "cumulativeByCode": aggregated["cumulativeByCode"],
        "operationalState": "PERSISTED_READ_ONLY",
        "patchReadiness": {
            selected_patch: readiness_by_patch.get(selected_patch)
            for selected_patch in sorted(expected_patches)
        },
        "healthByContext": health_by_context,
        "privacy": {
            "matchIds": False,
            "participantIds": False,
            "riotIds": False,
            "subjectHashes": False,
            "individualGroups": False,
        },
    }


def render_blockers_report(data: dict[str, Any]) -> str:
    totals = data["totals"]
    lines = [
        "Censo de bloqueios da extração corrente",
        f"medido: {'sim' if data['measured'] else 'não (sem pontos para estes filtros)'}",
        (
            f"filtros: patch={data['filters']['patch'] or 'todos'}, "
            f"fila={data['filters']['queueId'] or 'todas'}, "
            f"in-scope={data['filters']['inScopeOnly']}"
        ),
        (
            f"decisões: {totals['decisions']} | elegíveis: {totals['eligible']} "
            f"({totals['eligiblePct']}%, denominador {totals['decisions']}) | "
            f"inelegíveis: {totals['ineligible']} ({totals['ineligiblePct']}%, "
            f"denominador {totals['decisions']})"
        ),
        (
            f"dentro do recorte: {totals['inScope']} ({totals['inScopePct']}%, "
            f"denominador {totals['sourceDecisions']}); fora: {totals['outOfScope']}"
        ),
        f"extrações correntes por contexto: {data['versions'] or '-'}",
        (
            f"matches Riot importados: {data['importedMatches']}; sem versões correntes: "
            f"{data['matchesWithoutCurrentVersions']} ({data['currentVersionFailureCodes'] or '-'})"
        ),
        f"sem extração corrente: {data['missingCurrentExtractionMatches']}",
        "por patch/fila:",
        *(
            f"- {row['patch']}/{row['queueId']}: {row['measurementStatus']}; "
            f"extrações={row['currentExtractionMatches']}; decisões={row['decisions']}; "
            f"elegíveis={row['eligible']} ({row['eligiblePct']}%); "
            f"inelegíveis={row['ineligible']} ({row['ineligiblePct']}%)"
            for row in data["byPatchQueue"].values()
        ),
        f"saúde/preparo: {data.get('operationalState', 'NOT_QUERIED')}",
        "saúde persistida por patch/fila/fingerprint (sem recálculo):",
        *(
            f"- {row['patch']}/{row['queueId'] if row['queueId'] is not None else 'geral'} "
            f"{row['application']}/{row['sourcePatch']} fingerprint={row['rulesFingerprint']} "
            f"v{row['reconstructionVersion']} {row['verdict']} "
            f"n={row['cleanParticipants']} falhas={row['failures']} "
            f"m={row['contributingMatches']} WilsonUpper={row['wilsonUpper']} "
            f"invalidação={row['isInvalidation']}"
            for row in data.get("healthByContext", [])
        ),
        "readiness agregado por patch (denominador distinto das filas acima):",
        *(
            f"- {patch}: partidas={value['matches']}, participantesUpperBound="
            f"{value['participantsUpperBound']}, janelaAtual={value['inCurrentWindow']}"
            for patch, value in data.get("patchReadiness", {}).items()
            if value is not None
        ),
        "primeiro motivo por decisão inelegível (denominador próprio):",
    ]
    if data["firstReasonByItem"]:
        lines.extend(
            f"- {row['code']} | item={row['itemId']} ({row['itemName']}) | "
            f"{row['decisions']}/{row['denominator']} ({row['pctOfIneligible']}%)"
            for row in data["firstReasonByItem"]
        )
    else:
        lines.append("- nenhum")
    lines.append("marcas cumulativas por decisão (denominador: decisões selecionadas):")
    if data["cumulativeByCode"]:
        lines.extend(
            f"- {row['code']}: {row['decisions']}/{row['denominator']} ({row['pctOfDecisions']}%)"
            for row in data["cumulativeByCode"]
        )
    else:
        lines.append("- nenhuma")
    return "\n".join(lines)
