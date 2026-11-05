"""CC-505: traceable export of one analysis (JSON or self-contained HTML).

The report carries only what the analysis already allowed to show: pre-decision context
without match, participant or player identifiers; objective and its origin; scenario
(manual gold or not); engine and versions; alternatives with sourced explanations; known
limits; reviewer judgements without pseudonyms. With an attempt linked to the same
analysis it also separates the initial answer, hints used, the comparison and the
reflection. Free text is scrubbed of Riot IDs and key-like tokens, and every string in the
HTML is escaped; the page has no script and no external resource.
"""

from __future__ import annotations

import html
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.analyses.models import AnalysisModel
from riftlab.champions.models import ChampionModel
from riftlab.decisions.models import DecisionPointModel
from riftlab.learning.application import get_attempt
from riftlab.reviews.application import list_feedback
from riftlab.shared.errors import InvalidRequestError, NotFoundError

# v2 (CC-1217): revisor como rótulo por relatório ("Revisor 1, 2…"), nunca derivado do apelido.
REPORT_VERSION = "report_v2"
REMOVED = "[removido]"
_RIOT_ID = re.compile(r"[^\s#]{1,32}#[A-Za-z0-9]{2,5}\b")
_TOKEN = re.compile(r"\bRGAPI-[A-Za-z0-9-]+|\b[A-Za-z0-9_-]{32,}\b")

ENGINE_TEXT = {
    "POPULARITY": "Popularidade nas decisões de referência deste campeão nesta etapa da build",
    "RULES": "Regras explícitas (aproveitar componentes e custo restante)",
    "CONTEXTUAL_RANKER": "Modelo contextual registrado",
}
PRIORITY_TEXT = {
    "BALANCED": "Equilibrado",
    "SURVIVAL": "Sobrevivência",
    "DAMAGE": "Dano",
    "UTILITY": "Utilidade",
}
LIMITS = [
    "A ordem compara alternativas legais neste momento; não é a compra ótima nem garante vitória.",
    "Pontuações são relativas a esta decisão; não são probabilidades.",
    "A compra feita na partida é histórico, não gabarito.",
    "O objetivo só reordena alternativas pelos atributos do catálogo; "
    "não muda legalidade nem preço.",
    "Julgamentos de revisores são opiniões pela rubrica indicada, não nota automática do aluno.",
    "Nada deste relatório foi usado para treinar modelos.",
]


def scrub(text: str | None) -> str | None:
    if text is None:
        return None
    return _TOKEN.sub(REMOVED, _RIOT_ID.sub(REMOVED, text))


def _minute(ms: int | None) -> str | None:
    if ms is None:
        return None
    seconds = ms // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


async def _champion(session: AsyncSession, key: object) -> str | None:
    if not isinstance(key, int):
        return None
    return await session.scalar(
        select(ChampionModel.name).where(ChampionModel.champion_key == key).limit(1)
    )


async def build_report(
    session_factory: async_sessionmaker[AsyncSession],
    analysis_id: uuid.UUID,
    attempt_id: uuid.UUID | None,
    configs: Path,
    *,
    owner: uuid.UUID | None = None,
) -> dict[str, Any]:
    """`owner` (#11): julgamentos e tentativa do usuário da sessão; tentativa de outra pessoa
    responde como inexistente."""
    async with session_factory() as session:
        analysis = await session.get(AnalysisModel, analysis_id)
        if analysis is None:
            raise NotFoundError(f"análise {analysis_id} não encontrada")
        point = await session.get(DecisionPointModel, analysis.decision_id)
        values = ((point.features or {}).get("values", {})) if point else {}
        champion = await _champion(session, values.get("self_champion_id"))
        feedback = await list_feedback(session, analysis_id, owner)
    result = analysis.result
    names = {c["itemId"]: c.get("name") for c in result.get("candidates", [])}
    quality = result.get("quality", {})
    manual = quality.get("budgetSource") == "USER_PROVIDED"
    objective = result.get("objective", {})
    # O ref guardado é SHA-256 sem chave do apelido: no arquivo exportado ele deixaria testar o
    # apelido e ligar o revisor entre relatórios. Fora do sistema vale só a ordem neste relatório.
    labels: dict[str, str] = {}
    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "analysisId": str(analysis.id),
        "decision": {
            "patch": str(result.get("versions", {}).get("catalog", "")).split("/")[0] or None,
            "champion": champion,
            "time": _minute(point.event_timestamp_ms if point else None),
            "mainItemOrdinal": point.main_item_ordinal if point else None,
        },
        "analysis": {
            "status": result.get("status"),
            "engine": result.get("engine"),
            "engineText": ENGINE_TEXT.get(result.get("engine", ""), result.get("engine")),
            "objective": {
                "priority": objective.get("priority"),
                "label": PRIORITY_TEXT.get(
                    objective.get("priority", ""), objective.get("priority")
                ),
                "origin": "TEXT_CONFIRMED" if objective.get("parserVersion") else "FORM",
                "parserVersion": objective.get("parserVersion"),
            },
            "scenario": {
                "manual": manual,
                "userGold": quality.get("userGold") if manual else None,
                "label": "Cenário manual com ouro informado (não é o que aconteceu)"
                if manual
                else "Estado reconstruído da partida",
            },
            "budgetSource": quality.get("budgetSource"),
            "warnings": quality.get("warnings", []),
            "support": result.get("support", {}),
            "versions": result.get("versions", {}),
            "candidates": [
                {
                    "rank": c.get("rank"),
                    "baseRank": c.get("baseRank"),
                    "itemId": c["itemId"],
                    "name": c.get("name"),
                    "remainingCost": c.get("remainingCost"),
                    "affordability": c.get("affordability"),
                    "tags": c.get("tags", []),
                    "explanations": c.get("explanations", []),
                }
                for c in result.get("candidates", [])
            ],
            "scoreNote": result.get("scoreNote"),
        },
        "learning": None,
        "feedback": [
            {
                **f,
                "itemName": names.get(f["itemId"]),
                "reason": scrub(f["reason"]),
                "reviewerRef": labels.setdefault(f["reviewerRef"], f"Revisor {len(labels) + 1}"),
            }
            for f in feedback
        ],
        "limits": LIMITS,
    }
    if attempt_id is not None:
        state = await get_attempt(session_factory, attempt_id, configs, owner=owner)
        if state.get("analysisId") != str(analysis_id):
            raise InvalidRequestError("tentativa não pertence a esta análise")
        options = {o["itemId"]: o.get("name") for o in state["options"]}
        selected = state.get("selectedItemId")
        historical = state.get("historicalItemId")
        report["learning"] = {
            "mode": state.get("mode", "STUDY"),
            "optionsShown": [o.get("name") or f"Item {o['itemId']}" for o in state["options"]],
            "initialAnswer": "NÃO SEI"
            if state.get("skipped")
            else (options.get(selected) or (f"Item {selected}" if selected else None)),
            "rationale": scrub(state.get("rationale")),
            "hintLevel": state.get("hintLevel", 0),
            "hints": [h["text"] for h in state.get("hints", [])],
            "historicalPurchase": names.get(historical)
            or options.get(historical)
            or (f"Item {historical}" if historical else None),
            "reflection": scrub(state.get("reflection")),
        }
    return report


_CSS = """
body{font-family:system-ui,sans-serif;max-width:52rem;margin:2rem auto;padding:0 1rem;
color:#1b1b1f;background:#fff;line-height:1.5}
h1{font-size:1.5rem}h2{font-size:1.15rem;margin-top:2rem;border-bottom:1px solid #ccc}
.muted{color:#555}table{border-collapse:collapse;width:100%}
td,th{border:1px solid #ccc;padding:.35rem .5rem;text-align:left;vertical-align:top}
.tag{display:inline-block;border:1px solid #888;border-radius:.25rem;padding:0 .3rem;
font-size:.85rem}
@media (prefers-color-scheme: dark){body{background:#141418;color:#ececf1}
.muted{color:#b4b4bd}td,th{border-color:#444}h2{border-color:#444}}
"""


def _e(value: object) -> str:
    return html.escape("—" if value is None or value == "" else str(value), quote=True)


def render_html(report: dict[str, Any]) -> str:
    a, d = report["analysis"], report["decision"]
    rows = []
    for c in a["candidates"]:
        explanations = "".join(
            f"<li>{_e(x.get('text'))} <span class='muted'>fonte: {_e(x.get('source'))}</span></li>"
            for x in c["explanations"]
        )
        rows.append(
            f"<tr><td>{_e(c['rank'])}</td><td>{_e(c['name'] or 'Item ' + str(c['itemId']))}</td>"
            f"<td>{_e(c['remainingCost'])}</td><td>{_e(c['affordability'])}</td>"
            f"<td><ul>{explanations}</ul></td></tr>"
        )
    parts = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
        "style-src 'unsafe-inline'\">",
        f"<title>Relatório RiftLab {_e(report['analysisId'][:8])}</title><style>{_CSS}</style>",
        "</head><body>",
        "<h1>Relatório de análise de compra</h1>",
        f"<p class='muted'>Gerado em {_e(report['generatedAt'])} · {_e(report['reportVersion'])}"
        f" · análise {_e(report['analysisId'])}</p>",
        "<h2>Contexto antes da compra</h2><ul>",
        f"<li>Patch: {_e(d['patch'])}</li><li>Campeão: {_e(d['champion'])}</li>",
        f"<li>Momento: {_e(d['time'])}</li><li>Item principal nº: {_e(d['mainItemOrdinal'])}</li>",
        "</ul>",
        "<h2>Objetivo e cenário</h2><ul>",
        f"<li>Objetivo: {_e(a['objective']['label'])} — "
        + (
            f"de texto confirmado (interpretador {_e(a['objective']['parserVersion'])})"
            if a["objective"]["origin"] == "TEXT_CONFIRMED"
            else "escolhido no formulário"
        )
        + "</li>",
        f"<li>Cenário: {_e(a['scenario']['label'])}"
        + (f" — {_e(a['scenario']['userGold'])} de ouro" if a["scenario"]["manual"] else "")
        + "</li>",
        f"<li>Critério: {_e(a['engineText'])} ({_e(a['engine'])})</li>",
        f"<li>Estado da análise: {_e(a['status'])}</li></ul>",
    ]
    learning = report.get("learning")
    if learning:
        hints = "".join(f"<li>{_e(h)}</li>" for h in learning["hints"]) or "<li>Nenhuma</li>"
        mode = "transferência, sem pistas" if learning["mode"] == "TRANSFER" else "estudo"
        shown = ", ".join(learning["optionsShown"])
        parts += [
            "<h2>Resposta inicial</h2><ul>",
            f"<li>Modo: {mode}</li>",
            f"<li>Alternativas mostradas (ordem neutra): {_e(shown)}</li>",
            f"<li>Escolha: {_e(learning['initialAnswer'])}</li>",
            f"<li>Justificativa: {_e(learning['rationale'])}</li></ul>",
            f"<h2>Pistas consultadas</h2><p>Nível {_e(learning['hintLevel'])}</p><ul>{hints}</ul>",
        ]
    parts += [
        "<h2>Comparação</h2>",
        "<table><thead><tr><th>#</th><th>Alternativa</th><th>Ouro restante</th>"
        "<th>Orçamento</th><th>Justificativas</th></tr></thead><tbody>",
        *rows,
        "</tbody></table>",
    ]
    if not a["candidates"]:
        parts.append(
            "<p>Sem alternativas: "
            + _e(", ".join(a.get("support", {}).get("reasonCodes", [])) or "motivo não informado")
            + "</p>"
        )
    if learning:
        parts += [
            f"<p>Compra feita na partida (histórico, não gabarito): "
            f"{_e(learning['historicalPurchase'])}</p>",
            f"<h2>Reflexão</h2><p>{_e(learning['reflection'])}</p>",
        ]
    feedback = "".join(
        f"<tr><td>{_e(f['itemName'] or f['itemId'])}</td>"
        f"<td>{_e('abstenção' if f['grade'] is None else f['grade'])}</td>"
        f"<td>{_e(f['reason'])}</td><td>{_e(f['rubricVersion'])}</td>"
        f"<td>{_e(f['reviewerRef'])}</td></tr>"
        for f in report["feedback"]
    )
    parts += [
        "<h2>Julgamentos de revisores</h2>",
        "<table><thead><tr><th>Alternativa</th><th>Nota (0–3)</th><th>Motivo</th><th>Rubrica</th>"
        f"<th>Revisor</th></tr></thead><tbody>{feedback}</tbody></table>"
        if feedback
        else "<p>Nenhum julgamento registrado.</p>",
        "<h2>Versões</h2><ul>",
        *(f"<li>{_e(k)}: {_e(v)}</li>" for k, v in sorted(a["versions"].items())),
        "</ul><h2>Limites</h2><ul>",
        *(f"<li>{_e(limit)}</li>" for limit in report["limits"]),
        "</ul></body></html>",
    ]
    return "".join(parts)
