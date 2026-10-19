"""CC-303/304/305: create and read immutable analyses of one decision point.

Candidates always come from the legal candidate generator; the engine is
POPULARITY when the patch has a popularity artifact with support for the
(champion, main item ordinal) cell, otherwise RULES. The objective reorders
explicitly and the base order stays in the result. Same Idempotency-Key with the
same body returns the stored result; a different body is a 409.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.analyses.models import AnalysisModel
from riftlab.candidates.domain import (
    CandidateContext,
    CandidateInputError,
    UserBudget,
    enumerate_candidates,
)
from riftlab.candidates.rules import resolve_catalog_item_rules
from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.dataset.rows import RowsError, _catalog
from riftlab.decisions.features import build_pre_decision_view
from riftlab.decisions.models import DecisionPointModel
from riftlab.ml.registry import resolve_active
from riftlab.ranking.engine import (
    POPULARITY,
    RULES,
    CandidateFacts,
    DecisionFacts,
    as_payload,
    load_default,
    rank,
)
from riftlab.ranking.inheritance import (
    PopularityCatalog,
    filter_inherited_popularity,
    latest_previous_artifact,
)
from riftlab.shared.errors import ConflictError, NotFoundError
from riftlab.timeline.health import health_for_state
from riftlab.timeline.repository import SqlAlchemyTimelineRepository

ALGORITHM_VERSION = "analysis_v1"
REGISTRY_TASK = "purchase_decision"  # escopo do registro = patch do catálogo


def _support_level(supported: bool, reason_codes: list[str]) -> str:
    return "SUPPORTED" if supported and not reason_codes else "LIMITED"


def _ranking_version(popularity: Any, reference: dict[str, Any] | None, fallback: str) -> str:
    # versions.ranking tem max_length=40; "popularity_16_19_v2 (herdado)" usa 29.
    # A herança continua marcada por POPULARITY_INHERITED se o sufixo não couber.
    version = str(popularity.version or fallback)
    if reference is not None and reference.get("type") == "INHERITED":
        inherited = f"{version} (herdado)"
        return inherited if len(inherited) <= 40 else version
    return version


def _reference_payload(
    kind: str, popularity: Any, *, catalog_id: str | None = None, catalog_sha256: str | None = None
) -> dict[str, Any]:
    identity = popularity.source_catalog_identity or {}
    return {
        "type": kind,
        "patch": popularity.game_patch or "unknown",
        "rankingVersion": popularity.version,
        "datasetSha256": popularity.dataset_sha256,
        "catalogId": catalog_id,
        "catalogSha256": catalog_sha256,
        "dataDragonVersion": identity.get("dataDragonVersion"),
        "mappingVersion": identity.get("mappingVersion"),
        "rawHash": identity.get("rawHash"),
        "curationRulesVersion": identity.get("curationRulesVersion"),
    }


async def _popularity_for_analysis(
    session: AsyncSession,
    configs: Path,
    patch: str,
    decision: DecisionFacts,
    destination_bundle: Any,
    own: Any,
) -> tuple[Any, int, list[str], dict[str, Any] | None]:
    """Own supported artifact wins; otherwise try exactly the latest prior patch."""
    own_support = own.support(decision) if own is not None else 0
    if own is not None and own_support > 0:
        return own, own_support, [], _reference_payload("OWN", own)

    previous = latest_previous_artifact(configs, patch)
    if previous is None:
        reference = _reference_payload("OWN", own) if own is not None else None
        return None, 0, ["NO_POPULARITY_FOR_CELL"], reference

    identity = previous.source_catalog_identity
    source_bundle = None
    if isinstance(identity, dict) and isinstance(identity.get("sha256"), str):
        try:
            repository = SqlAlchemyCatalogRepository(session)
            catalog = await repository.find_catalog_version_by_content_hash(identity["sha256"])
            if catalog is not None:
                source_bundle = await _catalog(session, catalog.id)
        except RowsError:
            source_bundle = None

    result = filter_inherited_popularity(
        previous,
        (
            PopularityCatalog(
                source_bundle.catalog,
                {item.item_id: item for item in source_bundle.items},
                source_bundle.classification,
            )
            if source_bundle is not None
            else None
        ),
        PopularityCatalog(
            destination_bundle.catalog,
            {item.item_id: item for item in destination_bundle.items},
            destination_bundle.classification,
        ),
    )
    reference = _reference_payload(
        "UNAVAILABLE",
        previous,
        catalog_id=result.source_catalog_id,
        catalog_sha256=result.source_catalog_sha256,
    )
    if result.popularity is None:
        return None, 0, [result.reason_code or "POPULARITY_INHERITANCE_UNAVAILABLE"], reference
    support = result.popularity.support(decision)
    if support <= 0:
        return None, 0, ["NO_INHERITED_POPULARITY_FOR_CELL"], reference
    reference["type"] = "INHERITED"
    reference["catalogId"] = result.source_catalog_id
    reference["catalogSha256"] = result.source_catalog_sha256
    return result.popularity, support, ["POPULARITY_INHERITED"], reference


def request_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _empty(
    decision_id: uuid.UUID, objective: dict[str, Any], status: str, codes: list[str], catalog: str
) -> dict[str, Any]:
    return {
        "decisionId": str(decision_id),
        "status": status,
        "engine": RULES,
        "objective": objective,
        "versions": {
            "model": None,
            "catalog": catalog,
            "features": "feature_schema_v1",
            "candidates": ALGORITHM_VERSION,
        },
        "quality": {"budgetSource": "UNKNOWN", "frameAgeMs": None, "warnings": []},
        "support": {"level": "NONE", "reasonCodes": sorted(set(codes))},
        "candidates": [],
    }


async def _compute(
    session: AsyncSession, decision_id: uuid.UUID, body: dict[str, Any], configs: Path
) -> dict[str, Any]:
    objective = body["objective"]
    point = await session.get(DecisionPointModel, decision_id)
    if point is None:
        raise NotFoundError(f"decisão {decision_id} não encontrada")
    if not point.eligible:
        codes = [r.get("code", "INELIGIBLE") for r in point.reasons] or ["INELIGIBLE"]
        return _empty(decision_id, objective, "INSUFFICIENT_EVIDENCE", codes, "unknown")
    if point.catalog_id is None:
        return _empty(decision_id, objective, "UNSUPPORTED", ["MISSING_CATALOG"], "unknown")
    bundle = await _catalog(session, point.catalog_id)
    patch = bundle.catalog.game_patch
    catalog_label = f"{patch}/{bundle.catalog.data_dragon_version}"
    item_rules = await resolve_catalog_item_rules(session, configs / "item-rules", bundle)
    if item_rules is None:
        return _empty(decision_id, objective, "UNSUPPORTED", ["PATCH_UNSUPPORTED"], catalog_label)
    state = await SqlAlchemyTimelineRepository(session).get_decision_state(point.decision_state_id)
    values = (point.features or {}).get("values", {})
    champion = values.get("self_champion_id")
    if state is None or not isinstance(champion, int):
        return _empty(
            decision_id, objective, "INSUFFICIENT_EVIDENCE", ["INVALID_STATE"], catalog_label
        )
    provenance = state.quality.get("empiricalRules") or []
    health = await health_for_state(session, patch, point.reconstruction_version, provenance)
    inherited = any(r.get("application") == "INHERITED" for r in provenance)
    if inherited and health["status"] == "SUSPENDED":
        return _empty(
            decision_id,
            objective,
            "INSUFFICIENT_EVIDENCE",
            ["INHERITED_RULES_SUSPENDED"],
            catalog_label,
        )
    budget = body.get("budget")
    try:
        result = enumerate_candidates(
            CandidateContext(build_pre_decision_view(state), patch, 11, champion),
            bundle.catalog,
            bundle.items,
            bundle.classification,
            item_rules,
            user_budget=UserBudget(gold=int(budget["gold"])) if budget else None,
        )
    except CandidateInputError as exc:
        return _empty(decision_id, objective, "INSUFFICIENT_EVIDENCE", [exc.code], catalog_label)
    if not result.candidates:
        excluded = {
            code
            for entry in result.exclusions
            if bundle.classification.classify(entry.item_id).item_class.value == "MAIN"
            for code in entry.reason_codes
        }
        return _empty(
            decision_id,
            objective,
            "INSUFFICIENT_EVIDENCE",
            ["NO_LEGAL_CANDIDATES", *sorted(excluded)],
            catalog_label,
        )
    items = {item.item_id: item for item in bundle.items}
    facts = [
        CandidateFacts(
            c.item_id,
            items[c.item_id].attributes.get("name"),
            c.remaining_cost,
            c.affordability.value,
            c.evidence_codes,
            tuple(items[c.item_id].attributes.get("tags") or ()),
        )
        for c in result.candidates
    ]
    decision = DecisionFacts(champion, point.main_item_ordinal, None, None)
    own_popularity, rules, objective_cfg = load_default(configs, patch)
    (
        popularity,
        reference_decisions,
        popularity_reasons,
        reference_source,
    ) = await _popularity_for_analysis(session, configs, patch, decision, bundle, own_popularity)
    supported = popularity is not None and reference_decisions > 0
    engine = POPULARITY if supported else RULES
    ranked = rank(
        decision,
        facts,
        engine=engine,
        priority=objective["priority"],
        popularity=popularity,
        rules=rules,
        objective=objective_cfg,
    )
    reason_codes = list(popularity_reasons)
    # Herança (reconstrução v10 e legalidade): análise possível, mas com regras
    # confirmadas em outro patch; o motivo vai junto do resultado.
    if any(
        rule.get("application") == "INHERITED" for rule in state.quality.get("empiricalRules") or []
    ):
        reason_codes.append("RECONSTRUCTION_RULES_INHERITED")
        if health["status"] == "UNVERIFIED":
            reason_codes.append("INHERITED_RULES_UNVERIFIED")
    if item_rules.inherited_from is not None:
        reason_codes.append("ITEM_RULES_INHERITED")
    gold = (point.budget or {}).get("value") if isinstance(point.budget, dict) else None
    return {
        "decisionId": str(decision_id),
        "status": "AVAILABLE",
        "engine": engine,
        "objective": objective,
        "versions": {
            "model": None,
            "catalog": catalog_label,
            "features": "feature_schema_v1",
            "candidates": ALGORITHM_VERSION,
            "ranking": _ranking_version(popularity, reference_source, rules.version)
            if supported and popularity
            else rules.version,
            "objective": objective_cfg.version,
            "itemRules": f"{item_rules.inherited_from}.json (herdado)"
            if item_rules.inherited_from
            else f"{patch}.json",
        },
        "quality": {
            "budgetSource": "USER_PROVIDED"
            if budget
            else (point.budget or {}).get("origin", "UNKNOWN"),
            "frameAgeMs": (point.budget or {}).get("ageMs"),
            "warnings": [] if gold is not None or budget else ["BUDGET_UNKNOWN"],
            "userGold": int(budget["gold"]) if budget else None,
        },
        "support": {
            "level": _support_level(supported, reason_codes),
            "reasonCodes": reason_codes,
            "legalCandidates": len(facts),
            "referenceDecisions": reference_decisions,
            "referenceSource": reference_source,
        },
        "candidates": [as_payload(item) for item in ranked],
        "scoreNote": "Pontuações são relativas a esta decisão; não são probabilidades.",
    }


async def create_analysis(
    session_factory: async_sessionmaker[AsyncSession],
    body: dict[str, Any],
    idempotency_key: str,
    configs: Path,
) -> tuple[dict[str, Any], bool]:
    """(result, created). Raises ConflictError on a divergent body for the same key."""
    digest = request_hash(body)
    async with session_factory() as session:
        existing = await session.scalar(
            select(AnalysisModel).where(AnalysisModel.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if existing.request_sha256 != digest:
                raise ConflictError("IDEMPOTENCY_CONFLICT")
            return {"analysisId": str(existing.id), **existing.result}, False
        decision_id = uuid.UUID(str(body["decisionId"]))
        result = await _compute(session, decision_id, body, configs)
    patch = str(result["versions"].get("catalog", "")).split("/")[0]
    active = await resolve_active(session_factory, REGISTRY_TASK, patch)
    registry_version = active.version_id
    result["versions"]["registryVersion"] = str(registry_version) if registry_version else None
    result["versions"]["registryFallback"] = active.fallback_reason
    analysis_id = uuid.uuid4()
    try:
        async with session_factory.begin() as session:
            session.add(
                AnalysisModel(
                    id=analysis_id,
                    decision_id=decision_id,
                    idempotency_key=idempotency_key,
                    request_sha256=digest,
                    engine=result["engine"],
                    status=result["status"],
                    model_version_id=registry_version,
                    result=result,
                )
            )
    except IntegrityError:
        return await create_analysis(session_factory, body, idempotency_key, configs)
    return {"analysisId": str(analysis_id), **result}, True


async def get_analysis(
    session_factory: async_sessionmaker[AsyncSession], analysis_id: uuid.UUID
) -> dict[str, Any]:
    async with session_factory() as session:
        row = await session.get(AnalysisModel, analysis_id)
    if row is None:
        raise NotFoundError(f"análise {analysis_id} não encontrada")
    return {"analysisId": str(row.id), **row.result}
