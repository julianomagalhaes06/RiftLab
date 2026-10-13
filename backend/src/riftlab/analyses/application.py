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
from riftlab.candidates.rules import load_item_rules
from riftlab.dataset.rows import _catalog
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
from riftlab.shared.errors import ConflictError, NotFoundError
from riftlab.timeline.repository import SqlAlchemyTimelineRepository

ALGORITHM_VERSION = "analysis_v1"
REGISTRY_TASK = "purchase_decision"  # escopo do registro = patch do catálogo


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
    rules_path = configs / "item-rules" / f"{patch}.json"
    if not rules_path.exists():
        return _empty(decision_id, objective, "UNSUPPORTED", ["PATCH_UNSUPPORTED"], catalog_label)
    state = await SqlAlchemyTimelineRepository(session).get_decision_state(point.decision_state_id)
    values = (point.features or {}).get("values", {})
    champion = values.get("self_champion_id")
    if state is None or not isinstance(champion, int):
        return _empty(
            decision_id, objective, "INSUFFICIENT_EVIDENCE", ["INVALID_STATE"], catalog_label
        )
    budget = body.get("budget")
    try:
        result = enumerate_candidates(
            CandidateContext(build_pre_decision_view(state), patch, 11, champion),
            bundle.catalog,
            bundle.items,
            bundle.classification,
            load_item_rules(rules_path),
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
    popularity, rules, objective_cfg = load_default(configs, patch)
    supported = popularity is not None and popularity.support(decision) > 0
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
    reason_codes = [] if supported else ["NO_POPULARITY_FOR_CELL"]
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
            "ranking": popularity.version if supported and popularity else rules.version,
            "objective": objective_cfg.version,
            "itemRules": rules_path.name,
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
            "level": "SUPPORTED" if supported else "LIMITED",
            "reasonCodes": reason_codes,
            "legalCandidates": len(facts),
            "referenceDecisions": popularity.support(decision) if popularity else 0,
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
