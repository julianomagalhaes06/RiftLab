"""Serviço de explicação do late game (CC-1505): recupera, gera e valida. Só leitura no banco.

Fluxo: a análise já gravada (candidatos, gatilhos, bloco `late`) + o contexto situacional
recalculado da mesma forma da análise → fatos (`knowledge.explain.gather_facts`) → resposta.
Com o modelo local ligado (mesmo processo do interpretador de objetivo, rota `/explain`), a
resposta passa pelo validador; sem modelo, erro, tempo esgotado ou nenhuma frase válida, vale a
resposta determinística montada dos mesmos fatos. O texto da pergunta nunca é registrado.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.knowledge.base import KnowledgeBase
from riftlab.knowledge.explain import (
    EXPLANATION_VERSION,
    MAX_QUESTION_CHARS,
    build_prompt,
    deterministic_answer,
    gather_facts,
    question_facts,
    validate_answer,
)
from riftlab.shared.errors import ConflictError, NotFoundError

logger = logging.getLogger(__name__)
CARDS_FILE = "knowledge/cards-v1.yaml"


class LocalExplainer:
    provider = "LOCAL_MODEL"

    def __init__(self, url: str | None, socket_path: str | None, timeout: float) -> None:
        self.url = (url or "http://localhost/interpret").rsplit("/", 1)[0] + "/explain"
        self.socket_path = socket_path
        self.timeout = timeout

    async def generate(self, messages: list[dict[str, str]]) -> str:
        transport = httpx.AsyncHTTPTransport(uds=self.socket_path) if self.socket_path else None
        async with httpx.AsyncClient(transport=transport, timeout=self.timeout) as client:
            response = await client.post(self.url, json={"messages": messages})
        response.raise_for_status()
        body = response.json()
        raw = body.get("raw") if isinstance(body, dict) else None
        if not isinstance(raw, str):
            raise ValueError("resposta sem texto")
        return raw


def _knowledge(configs: Path, patch: str) -> KnowledgeBase | None:
    from riftlab.ranking.late_engine import _latest_dir

    found = _latest_dir(configs, patch)
    if found is None or not (found[1] / "knowledge.json").exists():
        return None
    return KnowledgeBase.load(found[1], configs / CARDS_FILE)


async def explain_analysis(
    session_factory: async_sessionmaker[AsyncSession],
    analysis_id: uuid.UUID,
    question: str | None,
    configs: Path,
    *,
    explainer: LocalExplainer | None,
) -> dict[str, Any]:
    from riftlab.analyses.application import get_analysis, late_context
    from riftlab.analyses.models import AnalysisModel
    from riftlab.decisions.models import DecisionPointModel
    from riftlab.profiles.domain import load_profiles

    if question is not None and (not question.strip() or len(question) > MAX_QUESTION_CHARS):
        raise ConflictError("QUESTION_INVALID")
    analysis = await get_analysis(session_factory, analysis_id)
    late = analysis.get("late")
    if not late or analysis.get("status") != "AVAILABLE":
        raise ConflictError("EXPLANATION_ONLY_FOR_LATE_ANALYSIS")
    async with session_factory() as session:
        row = await session.get(AnalysisModel, analysis_id)
        point = await session.get(DecisionPointModel, row.decision_id) if row else None
        if point is None:
            raise NotFoundError("decisão da análise não encontrada")
        patch = str(analysis["versions"]["catalog"]).split("/")[0]
        kb = _knowledge(configs, patch)
        profiles = load_profiles(configs, patch)
        if kb is None or profiles is None:
            raise ConflictError("KNOWLEDGE_UNAVAILABLE")
        built = await late_context(session, session_factory, point, profiles)
    if built is None:
        raise ConflictError("KNOWLEDGE_UNAVAILABLE")
    context, me, enemies, names = built
    facts = gather_facts(
        kb,
        analysis,
        context,
        champion_id=me.champion_id,
        role=me.role,
        enemy_champion_ids=enemies,
        names=names,
        items=profiles.items,
        champions=profiles.champions,
    )
    if question:
        facts = facts + question_facts(kb, question, facts)
    fallback = deterministic_answer(facts)
    result: dict[str, Any] = {
        "analysisId": str(analysis_id),
        "version": EXPLANATION_VERSION,
        "provider": "RULES",
        "status": "DETERMINISTIC",
        "sentences": fallback,
        "facts": [f.to_payload() for f in facts],
        "reasonCodes": [],
    }
    if explainer is None:
        result["reasonCodes"] = ["MODEL_DISABLED"]
        return result
    known = (
        [n for n in names.values() if n]
        + [c.title for c in kb.chunks.values() if c.kind == "ITEM"]
        + [c.title.split(" — ")[0] for c in kb.chunks.values() if c.kind == "ABILITY"]
    )
    try:
        raw = await asyncio.wait_for(
            explainer.generate(build_prompt(facts, question, int(late["ordinal"]))),
            explainer.timeout,
        )
    except TimeoutError:
        result["reasonCodes"] = ["TIMEOUT"]
        return result
    except Exception as exc:  # noqa: BLE001 — falha do provedor cai na resposta determinística
        logger.warning("explainer failed: %s", type(exc).__name__)
        result["reasonCodes"] = ["PROVIDER_ERROR"]
        return result
    valid, dropped = validate_answer(raw, facts, known)
    result["reasonCodes"] = sorted(set(dropped))
    if valid:
        result.update(provider="LOCAL_MODEL", status="GENERATED", sentences=valid)
    return result


def explainer_from_settings(settings: Any) -> LocalExplainer | None:
    if getattr(settings, "explainer", "rules") != "local_model":
        return None
    url = getattr(settings, "objective_model_url", None)
    socket_path = getattr(settings, "objective_model_socket_path", None)
    if not url and not socket_path:
        return None
    return LocalExplainer(
        url, socket_path, float(getattr(settings, "explainer_timeout_seconds", 60))
    )


__all__ = ["LocalExplainer", "explain_analysis", "explainer_from_settings", "json"]
