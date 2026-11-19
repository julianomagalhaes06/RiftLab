"""CC-303/304/305: create and read immutable analyses of one decision point.

Candidates always come from the legal candidate generator; the engine is
POPULARITY when the patch has a popularity artifact with support for the
(champion, main item ordinal) cell, otherwise RULES. The objective reorders
explicitly and the base order stays in the result. Same Idempotency-Key with the
same body returns the stored result; a different body is a 409.
"""

from __future__ import annotations

import functools
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
from riftlab.decisions.domain import IncompatibleStateError
from riftlab.decisions.features import PreDecisionView, build_pre_decision_view
from riftlab.decisions.models import DecisionPointModel
from riftlab.decisions.tolerance import tolerate
from riftlab.ingestion.models import MatchModel
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

LATE_FROM_ORDINAL = 3
ALGORITHM_VERSION = "analysis_v2"  # v2 (CC-1501/1504): âncora de compromisso e late game
FLEX_QUEUE = 440
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
    session: AsyncSession,
    decision_id: uuid.UUID,
    body: dict[str, Any],
    configs: Path,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    artifacts_dir: Path | None = None,
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
    # CC-1501: o contexto vem da âncora de compromisso (primeira compra depois do item
    # principal anterior). Extrações anteriores à seleção v3 não têm âncora: instante da compra.
    anchor = point.anchor if isinstance(point.anchor, dict) else None
    view_state = state
    if anchor is not None:
        anchor_state = await SqlAlchemyTimelineRepository(session).get_decision_state(
            uuid.UUID(str(anchor["stateId"]))
        )
        if anchor_state is None:
            return _empty(
                decision_id,
                objective,
                "INSUFFICIENT_EVIDENCE",
                ["ANCHOR_STATE_MISSING"],
                catalog_label,
            )
        view_state = anchor_state
    try:
        view: PreDecisionView = build_pre_decision_view(view_state)
    except IncompatibleStateError as exc:
        # Estado de outra versão da reconstrução (partida preparada antes de a versão mudar) ou
        # malformado: análise sem alternativas e com o motivo, nunca erro 500 (CC-1207).
        return _empty(decision_id, objective, "INSUFFICIENT_EVIDENCE", [exc.code], catalog_label)
    tolerated_inventory = False
    if anchor is not None:
        tolerated = tolerate(view, bundle.classification)
        if tolerated is None:
            return _empty(
                decision_id,
                objective,
                "INSUFFICIENT_EVIDENCE",
                ["INVENTORY_UNCERTAIN"],
                catalog_label,
            )
        view, tolerated_inventory = tolerated.view, tolerated.tolerated
    provenance = state.quality.get("empiricalRules") or []
    match = await session.get(MatchModel, point.match_id)
    queue = match.queue if match is not None else None
    health = await health_for_state(session, patch, point.reconstruction_version, provenance, queue)
    inherited = any(r.get("application") == "INHERITED" for r in provenance)
    # CC-1504: a análise só depende de itens principais e componentes. Se a conferência
    # restrita a eles aprova a herança, a suspensão pelo inventário completo não bloqueia.
    main_items_confirmed = inherited and health.get("mainVerdict") == "ACCEPT"
    if inherited and health["status"] == "SUSPENDED" and not main_items_confirmed:
        return _empty(
            decision_id,
            objective,
            "INSUFFICIENT_EVIDENCE",
            ["INHERITED_RULES_SUSPENDED"],
            catalog_label,
        )
    suspended_but_main = inherited and health["status"] == "SUSPENDED"
    budget = body.get("budget")
    try:
        result = enumerate_candidates(
            CandidateContext(view, patch, 11, champion),
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
    if (
        anchor is not None
        and (point.main_item_ordinal or 0) >= LATE_FROM_ORDINAL
        and session_factory is not None
    ):
        late = await _late_result(
            session,
            session_factory,
            point,
            view,
            result,
            bundle,
            objective,
            configs,
            artifacts_dir,
            catalog_label,
            item_rules,
            tolerated_inventory,
            body.get("budget"),
            suspended_but_main,
        )
        if late is not None:
            return late
    enemy_physical, enemy_magic = await _composition(session, point, configs, patch)
    decision = DecisionFacts(champion, point.main_item_ordinal, enemy_physical, enemy_magic)
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
    if tolerated_inventory:
        reason_codes.append("INVENTORY_TOLERATED_NON_MAIN")
    if suspended_but_main:
        reason_codes.append("RULES_CONFIRMED_FOR_MAIN_ITEMS")
    # CC-804: regras confirmadas só em Solo/Duo aplicadas no Flex sem medição própria
    # suficiente; um ACCEPT da fila 440 dispensa o aviso.
    if (
        queue == FLEX_QUEUE
        and provenance
        and not any(rule.get("appliedFromQueue") == FLEX_QUEUE for rule in provenance)
        and health.get("queue") == FLEX_QUEUE
        and health["status"] == "UNVERIFIED"
    ):
        reason_codes.append("RULES_MEASURED_IN_SOLO_DUO")
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
    *,
    artifacts_dir: Path | None = None,
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
        result = await _compute(
            session,
            decision_id,
            body,
            configs,
            session_factory=session_factory,
            artifacts_dir=artifacts_dir,
        )
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
        return await create_analysis(
            session_factory, body, idempotency_key, configs, artifacts_dir=artifacts_dir
        )
    return {"analysisId": str(analysis_id), **result}, True


async def get_analysis(
    session_factory: async_sessionmaker[AsyncSession], analysis_id: uuid.UUID
) -> dict[str, Any]:
    async with session_factory() as session:
        row = await session.get(AnalysisModel, analysis_id)
    if row is None:
        raise NotFoundError(f"análise {analysis_id} não encontrada")
    return {"analysisId": str(row.id), **row.result}


async def _composition(
    session: AsyncSession, point: DecisionPointModel, configs: Path, patch: str
) -> tuple[int | None, int | None]:
    """Inimigos de dano físico (atirador, lutador, assassino) e mágico (mago), pelas tags dos
    perfis de campeão do patch; (None, None) sem perfis (o termo de composição fica de fora)."""
    from riftlab.decisions.domain import ParticipantView
    from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
    from riftlab.profiles.domain import load_profiles

    profiles = load_profiles(configs, patch)
    if profiles is None:
        return None, None
    participants = [
        ParticipantView.from_participant(p)
        for p in await SqlAlchemyIngestionRepository(session).list_participants(point.match_id)
    ]
    me = next((p for p in participants if p.participant_id == point.participant_id), None)
    if me is None:
        return None, None
    enemies = [profiles.champions.get(p.champion_id) for p in participants if p.team != me.team]
    if any(e is None for e in enemies):
        return None, None
    physical = sum(bool({"Marksman", "Fighter", "Assassin"} & set(e.tags)) for e in enemies if e)
    magic = sum("Mage" in e.tags for e in enemies if e)
    return physical, magic


async def _late_result(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    point: DecisionPointModel,
    view: PreDecisionView,
    candidates: Any,
    bundle: Any,
    objective: dict[str, Any],
    configs: Path,
    artifacts_dir: Path | None,
    catalog_label: str,
    item_rules: Any,
    tolerated_inventory: bool,
    budget: Any,
    suspended_but_main: bool = False,
) -> dict[str, Any] | None:
    """Análise do 3º item principal em diante (CC-1504). None quando o patch não tem padrão de
    late game nem herdável: a análise segue pelo caminho antigo."""
    from riftlab.ranking.late_engine import late_candidates, load_setup

    prices = {item.item_id: item.price for item in bundle.items}
    if artifacts_dir is None:
        import os

        artifacts_dir = Path(os.environ.get("RIFTLAB_LATE_ARTIFACTS_DIR", "/app/artifacts/late"))
    setup = load_setup(configs, artifacts_dir, bundle.catalog.game_patch, prices)
    if setup is None:
        return None
    built = await late_context(
        session, session_factory, point, setup.profiles, anchor_ms=view.event_timestamp_ms
    )
    assert built is not None
    context, me, _, _ = built
    items = {item.item_id: item for item in bundle.items}
    row = {
        "championId": me.champion_id,
        "role": me.role,
        "ordinal": point.main_item_ordinal,
        "candidates": [
            {
                "itemId": c.item_id,
                "remainingCost": c.remaining_cost,
                "ownedComponent": "OWNED_COMPONENT_COMPATIBLE" in c.evidence_codes,
            }
            for c in candidates.candidates
        ],
        "context": context,
    }
    names: dict[int, str | None] = {i: items[i].attributes.get("name") for i in items}
    names.update(_portuguese_names(configs, bundle.catalog.game_patch))
    engine, ranked, block = late_candidates(
        setup,
        row,
        names,
        {i: tuple(items[i].attributes.get("tags") or ()) for i in items},
        {c.item_id: c.remaining_cost for c in candidates.candidates},
        {c.item_id: c.affordability.value for c in candidates.candidates},
        {c.item_id: c.evidence_codes for c in candidates.candidates},
    )
    _, _, objective_cfg = load_default(configs, bundle.catalog.game_patch)
    if objective.get("priority") not in (None, "BALANCED") and ranked:
        # Mesmo reordenamento explícito do formulário (CC-304), sobre a probabilidade.
        for entry in ranked:
            entry["score"] = round(
                entry["probability"]
                * (
                    1
                    + objective_cfg.weight
                    * objective_cfg.bonus(objective["priority"], _facts(entry))
                ),
                6,
            )
        ranked.sort(key=lambda e: (-e["score"], e["itemId"]))
        for rank, entry in enumerate(ranked, start=1):
            entry["rank"] = rank
    reason_codes = ["LATE_MODEL_GATE_FAILED"] if not block.get("gatePassed") else []
    if block.get("sourcePatch") != bundle.catalog.game_patch:
        reason_codes.append("LATE_MODEL_INHERITED")
    if tolerated_inventory:
        reason_codes.append("INVENTORY_TOLERATED_NON_MAIN")
    if suspended_but_main:
        reason_codes.append("RULES_CONFIRMED_FOR_MAIN_ITEMS")
    if item_rules.inherited_from is not None:
        reason_codes.append("ITEM_RULES_INHERITED")
    gold = (point.budget or {}).get("value") if isinstance(point.budget, dict) else None
    return {
        "decisionId": str(point.id),
        "status": "AVAILABLE" if ranked else "INSUFFICIENT_EVIDENCE",
        "engine": engine,
        "objective": objective,
        "versions": {
            "model": None,
            "catalog": catalog_label,
            "features": "situation_v1",
            "candidates": ALGORITHM_VERSION,
            "ranking": f"{setup.artifact.metadata.get('version')}/{block.get('sourcePatch')}",
            "objective": objective_cfg.version,
            "itemRules": f"{item_rules.inherited_from}.json (herdado)"
            if item_rules.inherited_from
            else f"{bundle.catalog.game_patch}.json",
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
            "level": "SUPPORTED" if block.get("gatePassed") and not reason_codes else "LIMITED",
            "reasonCodes": reason_codes or ([] if ranked else ["NO_LEGAL_CANDIDATES"]),
            "legalCandidates": len(candidates.candidates),
            "referenceDecisions": block.get("championSupport"),
            "referenceSource": None,
        },
        "candidates": ranked,
        "late": block,
        "scoreNote": "Probabilidades estimadas entre os itens plausíveis desta decisão; o padrão "
        "é a frequência do item para o campeão nesta etapa da build.",
    }


def _facts(entry: dict[str, Any]) -> CandidateFacts:
    return CandidateFacts(
        entry["itemId"],
        entry.get("name"),
        entry["remainingCost"],
        entry["affordability"],
        tuple(entry.get("evidenceCodes") or ()),
        tuple(entry.get("tags") or ()),
    )


async def late_context(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    point: DecisionPointModel,
    profiles: Any,
    *,
    anchor_ms: int | None = None,
) -> tuple[dict[str, Any], Any, list[int], dict[int, str | None]] | None:
    """Contexto situacional da decisão na âncora (o mesmo da análise e do treino), o
    participante, os campeões inimigos e os nomes dos itens do catálogo da decisão."""
    from riftlab.decisions.domain import ParticipantView
    from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
    from riftlab.matches.timeline import get_match_timeline
    from riftlab.ml.situation import SituationInput, situation_features

    anchor = point.anchor if isinstance(point.anchor, dict) else None
    if anchor_ms is None:
        if anchor is None:
            return None
        anchor_ms = int(anchor["timestampMs"])
    participants = tuple(
        ParticipantView.from_participant(p)
        for p in await SqlAlchemyIngestionRepository(session).list_participants(point.match_id)
    )
    me = next(p for p in participants if p.participant_id == point.participant_id)
    timeline = await get_match_timeline(session_factory, point.match_id)
    context = situation_features(
        SituationInput(
            participants,
            timeline.participants,
            timeline.kills,
            timeline.objectives,
            timeline.gold_frames,
        ),
        point.participant_id,
        anchor_ms,
        items=profiles.items,
        champions=profiles.champions,
    )
    names: dict[int, str | None] = {}
    if point.catalog_id is not None:
        bundle = await _catalog(session, point.catalog_id)
        names = {item.item_id: item.attributes.get("name") for item in bundle.items}
    enemies = [p.champion_id for p in participants if p.team != me.team]
    return context, me, enemies, names


def _portuguese_names(configs: Path, patch: str) -> dict[int, str]:
    """Nomes pt_BR dos itens (trechos do Data Dragon da base de conhecimento), quando houver."""
    from riftlab.ranking.late_engine import _latest_dir

    found = _latest_dir(configs, patch)
    path = found[1] / "knowledge.json" if found else None
    if path is None or not path.exists():
        return {}
    return dict(_names_from(str(path), path.stat().st_mtime_ns))


@functools.lru_cache(maxsize=4)
def _names_from(path: str, _mtime: int) -> tuple[tuple[int, str], ...]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(
        (int(c["itemId"]), c["title"])
        for c in document.get("chunks", [])
        if c.get("kind") == "ITEM" and isinstance(c.get("itemId"), int)
    )
