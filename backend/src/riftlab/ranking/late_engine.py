"""Motor de análise do late game (CC-1504): padrão, escolha da partida e desvios com gatilho.

A decisão é montada exatamente como no treino (`dataset.late_v2` + `ml.situation`): candidatos
legais na âncora de compromisso e contexto situacional anterior a ela. O artefato tem duas
partes: o padrão (contagens agregadas, versionado em `configs/late/<patch>/`) e o modelo
CatBoost (arquivo local em `late_artifacts_dir/<patch>/model.cbm`, fora do Git). Sem modelo, ou
com o gate reprovado, o motor serve só o padrão (LATE_PRIOR) e diz isso.

Cada gatilho é uma frase com o número real do contexto e a feature de onde veio; nenhuma frase
sem dado. O gatilho vem da diferença das contribuições (SHAP) entre o item recomendado e o item
padrão: só features de contexto ou de confronto que puxam a favor do recomendado.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from riftlab.ml.late_eval import LIFT_THRESHOLD, MIN_DEVIATION_P
from riftlab.profiles.domain import Profiles, load_profiles

LATE_CONTEXT = "LATE_CONTEXT"
LATE_PRIOR = "LATE_PRIOR"
LATE_ANALYSIS_VERSION = "late_analysis_v1"
MAX_TRIGGERS = 3


def _n(value: Any) -> float:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else math.nan
    )


def _plural(n: float, one: str, many: str) -> str:
    return one if round(n) == 1 else many


Template = Callable[[Mapping[str, Any], str], str | None]


def _t(fmt: Callable[[Mapping[str, Any], str], str | None]) -> Template:
    return fmt


def _share(ctx: Mapping[str, Any], key: str) -> float:
    value = _n(ctx.get(key))
    return 0.5 if math.isnan(value) else value


# Feature -> frase (português, com os números reais). None = sem dado para afirmar.
TRIGGERS: Mapping[str, Template] = {
    "m_antiheal_vs_sustain": _t(
        lambda c, item: (
            (
                f"O time inimigo tem {c['enemy_healers']:.0f} "
                f"{_plural(c['enemy_healers'], 'campeão', 'campeões')} com cura no kit"
                + (
                    f" e {c['enemy_items_sustain']:.0f}% de vampirismo em itens"
                    if _n(c.get("enemy_items_sustain")) > 0
                    else ""
                )
                + f"; {item} aplica Feridas Dolorosas."
            )
            if _n(c.get("enemy_healers")) > 0 or _n(c.get("enemy_items_sustain")) > 0
            else None
        )
    ),
    "m_armor_vs_physical": _t(
        lambda c, item: (
            f"O dano inimigo é {1 - _share(c, 'enemy_magic_share'):.0%} físico, com "
            f"{_n(c.get('enemy_items_ad')):.0f} de Dano de Ataque já em itens; {item} dá Armadura."
            if _share(c, "enemy_magic_share") < 0.5
            else None
        )
    ),
    "m_mr_vs_magic": _t(
        lambda c, item: (
            f"O dano inimigo é {_share(c, 'enemy_magic_share'):.0%} mágico, com "
            f"{_n(c.get('enemy_items_ap')):.0f} de Poder de Habilidade já em itens; {item} dá "
            "Resistência Mágica."
            if _share(c, "enemy_magic_share") > 0.5
            else None
        )
    ),
    "m_armorpen_vs_armor": _t(
        lambda c, item: (
            f"Os inimigos já somam {_n(c.get('enemy_items_armor')):.0f} de Armadura em "
            f"itens; {item} atravessa armadura."
            if _n(c.get("enemy_items_armor")) > 0
            else None
        )
    ),
    "m_magicpen_vs_mr": _t(
        lambda c, item: (
            f"Os inimigos já somam {_n(c.get('enemy_items_mr')):.0f} de Resistência "
            f"Mágica em itens; {item} atravessa resistência mágica."
            if _n(c.get("enemy_items_mr")) > 0
            else None
        )
    ),
    "m_stasis_vs_threat": _t(
        lambda c, item: (
            f"O time inimigo tem {_n(c.get('enemy_assassin')):.0f} assassino(s)"
            + (
                f" e você morreu {_n(c.get('deaths_to_assassin')):.0f} vez(es) para eles"
                if _n(c.get("deaths_to_assassin")) > 0
                else ""
            )
            + f"; {item} dá Estase."
        )
    ),
    "m_spellshield_vs_cc": _t(
        lambda c, item: (
            f"Os inimigos têm {_n(c.get('enemy_hard_cc')):.0f} habilidades de controle "
            f"de grupo; {item} bloqueia uma habilidade."
        )
    ),
    "m_cleanse_vs_cc": _t(
        lambda c, item: (
            f"Os inimigos têm {_n(c.get('enemy_hard_cc')):.0f} habilidades de controle "
            f"de grupo; {item} remove controle de grupo."
        )
    ),
    "m_revive_vs_deaths": _t(
        lambda c, item: (
            f"Você já morreu {_n(c.get('self_deaths')):.0f} vezes; {item} revive."
            if _n(c.get("self_deaths")) > 0
            else None
        )
    ),
    "m_antishield_vs_shields": _t(
        lambda c, item: (
            f"O time inimigo tem {_n(c.get('enemy_shielders')):.0f} campeão(ões) com "
            f"escudo; {item} reduz escudos."
            if _n(c.get("enemy_shielders")) > 0
            else None
        )
    ),
    "m_anti_as_vs_attackers": _t(
        lambda c, item: (
            f"O time inimigo tem {_n(c.get('enemy_marksman')):.0f} atirador(es); {item} "
            "reduz a velocidade de ataque deles."
            if _n(c.get("enemy_marksman")) > 0
            else None
        )
    ),
    "m_hp_vs_burst": _t(
        lambda c, item: (
            f"O inimigo mais forte já tem {_n(c.get('top_enemy_kills')):.0f} abates; "
            f"{item} dá vida."
            if _n(c.get("top_enemy_kills")) > 0
            else None
        )
    ),
    "deaths_to_magic": _t(
        lambda c, item: (
            f"{_n(c.get('deaths_to_magic')):.0f} das suas mortes foram para dano mágico."
            if _n(c.get("deaths_to_magic")) > 0
            else None
        )
    ),
    "deaths_to_physical": _t(
        lambda c, item: (
            f"{_n(c.get('deaths_to_physical')):.0f} das suas mortes foram para dano físico."
            if _n(c.get("deaths_to_physical")) > 0
            else None
        )
    ),
    "enemy_healers": _t(
        lambda c, item: (
            f"O time inimigo tem {_n(c.get('enemy_healers')):.0f} campeão(ões) com cura."
            if _n(c.get("enemy_healers")) > 0
            else None
        )
    ),
    "enemy_items_armor": _t(
        lambda c, item: (
            f"Os inimigos já somam {_n(c.get('enemy_items_armor')):.0f} de Armadura em itens."
            if _n(c.get("enemy_items_armor")) > 0
            else None
        )
    ),
    "enemy_items_mr": _t(
        lambda c, item: (
            f"Os inimigos já somam {_n(c.get('enemy_items_mr')):.0f} de Resistência Mágica "
            "em itens."
            if _n(c.get("enemy_items_mr")) > 0
            else None
        )
    ),
    "enemy_magic_share": _t(
        lambda c, item: f"O dano inimigo é {_share(c, 'enemy_magic_share'):.0%} mágico."
    ),
    "team_gold_diff": _t(
        lambda c, item: (
            (
                f"Seu time está {abs(_n(c.get('team_gold_diff'))):.0f} de ouro "
                + ("à frente." if _n(c.get("team_gold_diff")) >= 0 else "atrás.")
            )
            if not math.isnan(_n(c.get("team_gold_diff")))
            else None
        )
    ),
    "self_deaths": _t(
        lambda c, item: (
            f"Você já morreu {_n(c.get('self_deaths')):.0f} vezes."
            if _n(c.get("self_deaths")) > 0
            else None
        )
    ),
}


@dataclass(frozen=True)
class LateSetup:
    """O que o motor precisa de um patch: artefato (padrão + modelo opcional) e perfis."""

    artifact: Any
    profiles: Profiles
    gate_passed: bool
    source_patch: str
    claim_strong: bool


def late_dir(configs: Path, patch: str) -> Path:
    return configs / "late" / patch


def _latest_dir(configs: Path, patch: str) -> tuple[str, Path] | None:
    """O artefato do próprio patch; senão o do patch mais próximo (empate: o anterior). Itens
    mudam nos dois sentidos, então o vizinho mais perto representa melhor a partida."""
    from riftlab.shared.numeric import patch_order_key

    own = late_dir(configs, patch)
    if (own / "prior.json").exists():
        return patch, own
    key = patch_order_key(patch)
    if key is None:
        return None

    def distance(other: tuple[int, int]) -> tuple[int, int]:
        major, minor = key
        gap = abs((other[0] - major) * 30 + (other[1] - minor))
        return gap, 0 if other < key else 1

    candidates = sorted(
        (distance(k), p.name, p)
        for p in (configs / "late").glob("*")
        if (p / "prior.json").exists() and (k := patch_order_key(p.name)) is not None
    )
    return (candidates[0][1], candidates[0][2]) if candidates else None


@lru_cache(maxsize=8)
def _load(
    configs: str, artifacts: str, patch: str, prices_key: tuple[tuple[int, int], ...]
) -> LateSetup | None:
    from riftlab.ml.late_model import LateArtifact

    found = _latest_dir(Path(configs), patch)
    if found is None:
        return None
    source_patch, directory = found
    profiles = load_profiles(Path(configs), source_patch)
    if profiles is None:
        return None
    metadata = json.loads((directory / "metadata.json").read_text())
    model_dir = Path(artifacts) / source_patch
    gate = metadata.get("gate") or {}
    gate_passed = bool(gate.get("passed"))
    staging = directory
    if gate_passed and (model_dir / "model.cbm").exists():
        staging = model_dir
        # O metadado do diretório local precisa ser o mesmo artefato do versionado.
        local = json.loads((model_dir / "metadata.json").read_text())
        if local.get("rowsSha256") != metadata.get("rowsSha256"):
            staging = directory
    artifact = LateArtifact.load(staging, profiles.items, dict(prices_key))
    if staging is directory:
        artifact.model = None
    return LateSetup(
        artifact,
        profiles,
        gate_passed and artifact.model is not None,
        source_patch,
        bool(gate.get("claimStrong")),
    )


def load_setup(
    configs: Path, artifacts: Path, patch: str, prices: Mapping[int, int]
) -> LateSetup | None:
    return _load(str(configs), str(artifacts), patch, tuple(sorted(prices.items())))


def _explain(
    setup: LateSetup,
    row: Mapping[str, Any],
    ids: Sequence[int],
    chosen: int,
    reference: int,
    names: Mapping[int, str | None],
) -> list[dict[str, str]]:
    """Frases do porquê `chosen` sobe acima de `reference` nesta partida."""
    if setup.artifact.model is None or chosen == reference:
        return []
    try:
        shap_ids, shap = setup.artifact.contributions(row)
    except Exception:  # noqa: BLE001 — sem SHAP, a análise segue sem gatilho
        return []
    feature_names = list(setup.artifact.model.feature_names_)
    a, b = list(shap_ids).index(chosen), list(shap_ids).index(reference)
    diff = shap[a] - shap[b]
    order = np.argsort(-diff)
    out: list[dict[str, str]] = []
    item = names.get(chosen) or f"o item {chosen}"
    context = row["context"]
    for index in order:
        if diff[index] <= 0 or len(out) >= MAX_TRIGGERS:
            break
        feature = feature_names[index]
        template = TRIGGERS.get(feature)
        if template is None:
            continue
        try:
            text = template(context, item)
        except (KeyError, TypeError, ValueError):
            text = None
        if text:
            out.append({"text": text, "source": f"{LATE_ANALYSIS_VERSION}.{feature}"})
    return out


def late_candidates(
    setup: LateSetup,
    row: Mapping[str, Any],
    names: Mapping[int, str | None],
    tags: Mapping[int, Sequence[str]],
    remaining: Mapping[int, int],
    affordability: Mapping[int, str],
    evidence: Mapping[int, Sequence[str]],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """(motor, candidatos do contrato, bloco `late`)."""
    ids, base, ctx = setup.artifact.score(row)
    engine = LATE_CONTEXT if setup.gate_passed else LATE_PRIOR
    probs = ctx if setup.gate_passed else base
    if not ids:
        return engine, [], {}
    default = ids[int(np.argmax(base))]
    order = sorted(range(len(ids)), key=lambda i: (-probs[i], ids[i]))
    chosen = [ids[i] for i in order[:3]]
    if default not in chosen:
        chosen.append(default)
    deviations = (
        [
            ids[i]
            for i in order
            if ids[i] != default
            and probs[i] >= MIN_DEVIATION_P
            and math.log(probs[i] / max(base[i], 1e-12)) >= LIFT_THRESHOLD
        ][:2]
        if setup.gate_passed
        else []
    )
    out: list[dict[str, Any]] = []
    for rank, item_id in enumerate(chosen, start=1):
        i = ids.index(item_id)
        explanations = [
            {
                "text": f"Faltam {remaining[item_id]} de ouro para completar o item.",
                "source": "candidates.remainingCost",
            }
        ]
        if item_id == default:
            explanations.append(
                {
                    "text": f"É o item mais comum para este campeão como "
                    f"{row['ordinal']}º item principal ({base[i]:.0%} no padrão).",
                    "source": f"{setup.artifact.prior.version}",
                }
            )
        else:
            explanations.extend(_explain(setup, row, ids, item_id, default, names))
        lift = math.log(max(probs[i], 1e-12) / max(base[i], 1e-12))
        out.append(
            {
                "itemId": item_id,
                "name": names.get(item_id),
                "rank": rank,
                "baseRank": sorted(range(len(ids)), key=lambda j: (-base[j], ids[j])).index(i) + 1,
                "score": round(float(probs[i]), 6),
                "remainingCost": remaining[item_id],
                "affordability": affordability[item_id],
                "evidenceCodes": list(evidence[item_id]),
                "tags": list(tags.get(item_id) or ()),
                "explanations": explanations[:4],
                "probability": round(float(probs[i]), 4),
                "baseProbability": round(float(base[i]), 4),
                "lift": round(lift, 4),
                "isDefault": item_id == default,
                "isDeviation": item_id in deviations,
            }
        )
    block = {
        "version": LATE_ANALYSIS_VERSION,
        "ordinal": row["ordinal"],
        "defaultItemId": default,
        "deviationItemIds": deviations,
        "gatePassed": setup.gate_passed,
        "claimStrong": setup.claim_strong and setup.gate_passed,
        "sourcePatch": setup.source_patch,
        "plausibleCandidates": len(ids),
        "championSupport": setup.artifact.prior.support(
            int(row["championId"]), int(row["ordinal"])
        ),
    }
    return engine, out, block


__all__ = [
    "LATE_ANALYSIS_VERSION",
    "LATE_CONTEXT",
    "LATE_PRIOR",
    "TRIGGERS",
    "LateSetup",
    "late_candidates",
    "late_dir",
    "load_setup",
]
