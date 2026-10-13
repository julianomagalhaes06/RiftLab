"""CC-602: amostra cega para revisão de especialistas e análise das notas.

A amostra sai só do teste reservado do dataset de planejamento com contexto (CC-408) e nada
aqui treina, seleciona ou ajusta modelo. Cada estado mostra apenas o contexto anterior à
âncora e uma lista curta de itens, misturados e sem rótulo: o top-3 da popularidade (critério
ativo), o top-1 do ranker CatBoost, a compra feita na partida e um item legal sorteado como
controle. A folha do revisor nunca traz partida, participante, compra histórica nem sistema;
isso fica numa chave privada usada só na análise.

A análise separa imitação (a compra histórica no topo) de pertinência (nota dos revisores) e
reporta concordância ponderada, distribuição de notas e preferências com intervalos por
bootstrap de estados.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any

RUBRIC_VERSION = "expert_rubric_v1"
REVIEW_SEED = 602
PER_STRATUM = 12
GRADES = (0, 1, 2, 3)
RESAMPLES = 2000
ROLES = ("popularity_top1", "popularity_top3", "model_top1", "historical", "control")

Scorer = Callable[[Mapping[str, Any]], list[float]]

ITEM_TAG_TEXT = {
    "armor": "armadura",
    "attackspeed": "velocidade de ataque",
    "criticalstrike": "acerto crítico",
    "damage": "dano físico",
    "health": "vida",
    "lifesteal": "roubo de vida",
    "spellblock": "resistência mágica",
    "spelldamage": "poder de habilidade",
}
ROLE_TEXT = {
    "TOP": "rota superior",
    "JUNGLE": "selva",
    "MIDDLE": "rota do meio",
    "BOTTOM": "rota inferior (atirador)",
    "UTILITY": "suporte",
}


def _digest(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def select_states(
    rows: Sequence[Mapping[str, Any]],
    seed: int = REVIEW_SEED,
    per_stratum: int = PER_STRATUM,
    splits: Sequence[str] = ("test",),
    ordinals: Sequence[int] | None = None,
) -> list[Mapping[str, Any]]:
    """Até `per_stratum` decisões por (campeão, ordem do item), escolhidas por hash, só das
    partes indicadas (nunca o treino) e, se pedido, só de algumas etapas da build."""
    if "train" in splits:
        raise ValueError("a revisão nunca usa decisões de treino")
    strata: dict[tuple[int, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if ordinals is not None and int(row["mainItemOrdinal"]) not in ordinals:
            continue
        if row["split"] in splits and row.get("candidates") and row.get("targetLegal"):
            strata[(int(row["championId"]), int(row["mainItemOrdinal"]))].append(row)
    chosen: list[Mapping[str, Any]] = []
    for key in sorted(strata):
        ordered = sorted(
            strata[key],
            key=lambda r: _digest(seed, r["matchId"], r["participantId"], r["mainItemOrdinal"]),
        )
        chosen.extend(ordered[:per_stratum])
    return sorted(
        chosen,
        key=lambda r: _digest(
            seed, "order", r["matchId"], r["participantId"], r["mainItemOrdinal"]
        ),
    )


def _ranked(row: Mapping[str, Any], scores: Sequence[float]) -> list[int]:
    ids = [int(c["itemId"]) for c in row["candidates"]]
    return [item for _, item in sorted(zip(scores, ids, strict=True), key=lambda p: (-p[0], p[1]))]


def items_to_grade(
    row: Mapping[str, Any], popularity: Scorer, model: Scorer, rng: random.Random
) -> tuple[list[int], dict[int, dict[str, Any]]]:
    """Itens da folha (ordem embaralhada) e, à parte, a origem de cada um."""
    by_popularity = _ranked(row, popularity(row))
    by_model = _ranked(row, model(row))
    roles: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"popularityRank": None, "modelTop1": False, "historical": False, "control": False}
    )
    for rank, item in enumerate(by_popularity[:3], 1):
        roles[item]["popularityRank"] = rank
    roles[by_model[0]]["modelTop1"] = True
    roles[int(row["targetItemId"])]["historical"] = True
    others = sorted(int(c["itemId"]) for c in row["candidates"] if int(c["itemId"]) not in roles)
    roles[rng.choice(others)]["control"] = True
    order = sorted(roles)
    rng.shuffle(order)
    return order, {item: dict(roles[item]) for item in order}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        return None
    return float(value)


def describe_state(
    row: Mapping[str, Any],
    state_id: str,
    items: Sequence[int],
    champion_names: Mapping[int, str],
    item_names: Mapping[int, str],
    item_costs: Mapping[int, int],
) -> dict[str, Any]:
    """Contexto anterior à âncora em linguagem de jogo; sem partida, participante ou resultado."""
    features, context = row["features"], row.get("context") or {}

    def champion(key: Any) -> str:
        try:
            return champion_names.get(int(key), f"Campeão {key}")
        except (TypeError, ValueError):
            return "desconhecido"

    remaining = {int(c["itemId"]): c.get("remainingCost") for c in row["candidates"]}
    inventory: Counter[int] = Counter()
    for entry in features.get("inventory_items") or []:  # pares [item, quantidade]
        item_id, quantity = (entry[0], entry[1]) if isinstance(entry, (list, tuple)) else (entry, 1)
        inventory[int(item_id)] += int(quantity)
    enemy_items = {
        ITEM_TAG_TEXT[tag]: int(count)
        for tag in ITEM_TAG_TEXT
        if (count := _number(context.get(f"enemy_items_{tag}"))) and count > 0
    }
    ordinal = int(row["mainItemOrdinal"])
    minute = int((features.get("game_time_ms") or 0) // 60000)
    return {
        "stateId": state_id,
        "champion": champion(row["championId"]),
        "role": ROLE_TEXT.get(str(features.get("self_role")), "função desconhecida"),
        "stage": "Antes da primeira compra da partida"
        if ordinal == 1
        else f"Logo depois de completar o 1º item principal (minuto {minute})",
        "mainItemOrdinal": ordinal,
        "minute": minute,
        "level": features.get("level"),
        "allies": [champion(c) for c in features.get("ally_champion_ids") or []],
        "enemies": [champion(c) for c in features.get("enemy_champion_ids") or []],
        "laneOpponent": champion(context["lane_opponent_champion_id"])
        if context.get("lane_opponent_champion_id") not in (None, "NONE")
        else None,
        "inventory": [
            {"name": item_names.get(i, f"Item {i}"), "quantity": q}
            for i, q in sorted(inventory.items())
        ],
        "enemyCompletedItems": _number(context.get("enemy_main_items")),
        "enemyItemTags": enemy_items,
        "allyCompletedItems": _number(context.get("ally_main_items")),
        "killDiff": _number(context.get("kill_diff")),
        "teamGoldDiff": _number(context.get("team_gold_diff")),
        "items": [
            {
                "itemId": i,
                "name": item_names.get(i, f"Item {i}"),
                "totalCost": item_costs.get(i),
                "remainingCost": remaining.get(i),
            }
            for i in items
        ],
    }


def build_sample(
    rows: Sequence[Mapping[str, Any]],
    popularity: Scorer,
    model: Scorer,
    champion_names: Mapping[int, str],
    item_names: Mapping[int, str],
    item_costs: Mapping[int, int],
    seed: int = REVIEW_SEED,
    per_stratum: int = PER_STRATUM,
    synthetic: bool = False,
    splits: Sequence[str] = ("test",),
    ordinals: Sequence[int] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Folha cega dos revisores e chave privada com origem dos itens e identidade da decisão."""
    rng = random.Random(seed)
    states, key_states = [], {}
    for index, row in enumerate(select_states(rows, seed, per_stratum, splits, ordinals), 1):
        state_id = f"R{index:02d}"
        order, roles = items_to_grade(row, popularity, model, rng)
        states.append(describe_state(row, state_id, order, champion_names, item_names, item_costs))
        key_states[state_id] = {
            "matchId": row["matchId"],
            "participantId": row["participantId"],
            "championId": row["championId"],
            "mainItemOrdinal": row["mainItemOrdinal"],
            "split": row["split"],
            "targetItemId": row["targetItemId"],
            "popularityRanking": _ranked(row, popularity(row))[:10],
            "items": {str(item): roles[item] for item in order},
        }
    common = {"schemaVersion": 1, "rubric": RUBRIC_VERSION, "seed": seed, "synthetic": synthetic}
    sheet = {**common, "states": states}
    key = {**common, "sheetSha256": _digest(states), "states": key_states}
    return sheet, key


def quadratic_kappa(
    pairs: Sequence[tuple[int, int]], categories: Sequence[int] = GRADES
) -> float | None:
    """Kappa de Cohen com pesos quadráticos; None sem variação esperada."""
    if not pairs:
        return None
    k = len(categories)
    index = {c: i for i, c in enumerate(categories)}
    observed = [[0.0] * k for _ in range(k)]
    for a, b in pairs:
        observed[index[a]][index[b]] += 1
    n = len(pairs)
    rows_m = [sum(observed[i]) for i in range(k)]
    cols_m = [sum(observed[i][j] for i in range(k)) for j in range(k)]
    weight = [[(i - j) ** 2 / (k - 1) ** 2 for j in range(k)] for i in range(k)]
    num = sum(weight[i][j] * observed[i][j] for i in range(k) for j in range(k))
    den = sum(weight[i][j] * rows_m[i] * cols_m[j] / n for i in range(k) for j in range(k))
    return None if den == 0 else 1 - num / den


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _bootstrap(
    states: Sequence[str],
    statistic: Callable[[Sequence[str]], float | None],
    resamples: int,
    seed: int,
) -> list[float] | None:
    rng = random.Random(seed)
    values = []
    for _ in range(resamples):
        drawn = [states[rng.randrange(len(states))] for _ in states]
        value = statistic(drawn)
        if value is not None:
            values.append(value)
    if not values:
        return None
    return [round(_percentile(values, 0.025), 4), round(_percentile(values, 0.975), 4)]


def _consensus(
    grades: Mapping[str, Mapping[str, Mapping[str, int | None]]],
) -> dict[tuple[str, str], float]:
    """Nota média por (estado, item) entre os revisores que não se abstiveram."""
    collected: dict[tuple[str, str], list[int]] = defaultdict(list)
    for per_state in grades.values():
        for state, items in per_state.items():
            for item, grade in items.items():
                if grade is not None:
                    collected[(state, item)].append(int(grade))
    return {pair: sum(v) / len(v) for pair, v in collected.items()}


def _items_with(key_state: Mapping[str, Any], role: str) -> list[str]:
    def has(meta: Mapping[str, Any]) -> bool:
        rank = meta["popularityRank"]
        return {
            "popularity_top1": rank == 1,
            "popularity_top3": rank is not None,
            "model_top1": meta["modelTop1"],
            "historical": meta["historical"],
            "control": meta["control"],
        }[role]

    return [item for item, meta in key_state["items"].items() if has(meta)]


def analyze(
    key: Mapping[str, Any],
    answers: Mapping[str, Mapping[str, Any]],
    resamples: int = RESAMPLES,
    seed: int = REVIEW_SEED,
) -> dict[str, Any]:
    """Agregado sem identidade: concordância, distribuição, pertinência por origem e imitação."""
    key_states = key["states"]
    grades = {ref: dict(a.get("grades") or {}) for ref, a in answers.items()}
    reviewers = sorted(grades)
    consensus = _consensus(grades)
    states = sorted(s for s in key_states if any(s in grades[r] for r in reviewers))

    per_reviewer = []
    for label, ref in zip("ABCDEFGH", reviewers, strict=False):
        values = [g for items in grades[ref].values() for g in items.values()]
        per_reviewer.append(
            {
                "ref": label,
                "experience": (answers[ref].get("profile") or {}).get("experience"),
                "graded": sum(g is not None for g in values),
                "abstained": sum(g is None for g in values),
                "distribution": {str(g): sum(v == g for v in values) for g in GRADES},
            }
        )

    def pairs_for(selected: Sequence[str]) -> list[tuple[int, int]]:
        if len(reviewers) < 2:
            return []
        a, b = grades[reviewers[0]], grades[reviewers[1]]
        found = []
        for state in selected:
            for item, grade in (a.get(state) or {}).items():
                other = (b.get(state) or {}).get(item)
                if grade is not None and other is not None:
                    found.append((int(grade), int(other)))
        return found

    pairs = pairs_for(states)
    agreement = {
        "pairs": len(pairs),
        "exact": round(sum(a == b for a, b in pairs) / len(pairs), 4) if pairs else None,
        "withinOne": round(sum(abs(a - b) <= 1 for a, b in pairs) / len(pairs), 4)
        if pairs
        else None,
        "weightedKappa": None if (k := quadratic_kappa(pairs)) is None else round(k, 4),
        "ci95": _bootstrap(states, lambda s: quadratic_kappa(pairs_for(s)), resamples, seed)
        if pairs
        else None,
    }

    def role_mean(selected: Sequence[str], role: str) -> float | None:
        values = [
            consensus[(s, item)]
            for s in selected
            for item in _items_with(key_states[s], role)
            if (s, item) in consensus
        ]
        return sum(values) / len(values) if values else None

    def role_statistic(role: str) -> Callable[[Sequence[str]], float | None]:
        return lambda selected: role_mean(selected, role)

    pertinence = {}
    for role in ROLES:
        mean = role_mean(states, role)
        pertinence[role] = {
            "mean": None if mean is None else round(mean, 3),
            "n": sum(
                (s, item) in consensus for s in states for item in _items_with(key_states[s], role)
            ),
            "ci95": _bootstrap(states, role_statistic(role), resamples, seed),
        }

    def preference(first: str, second: str) -> dict[str, Any]:
        diffs: dict[str, float] = {}
        for s in states:
            a_items, b_items = _items_with(key_states[s], first), _items_with(key_states[s], second)
            if not a_items or not b_items or set(a_items) == set(b_items):
                continue
            a = [consensus[(s, i)] for i in a_items if (s, i) in consensus]
            b = [consensus[(s, i)] for i in b_items if (s, i) in consensus]
            if a and b:
                diffs[s] = sum(a) / len(a) - sum(b) / len(b)
        values = list(diffs.values())
        return {
            "n": len(values),
            "wins": sum(v > 0 for v in values),
            "ties": sum(v == 0 for v in values),
            "losses": sum(v < 0 for v in values),
            "meanDiff": round(sum(values) / len(values), 3) if values else None,
            "ci95": _bootstrap(
                list(diffs),
                lambda sel: sum(diffs[x] for x in sel) / len(sel) if sel else None,
                resamples,
                seed,
            )
            if values
            else None,
        }

    hits = [key_states[s]["popularityRanking"][0] == key_states[s]["targetItemId"] for s in states]
    reciprocal = []
    for s in states:
        ranking = key_states[s]["popularityRanking"]
        target = key_states[s]["targetItemId"]
        reciprocal.append(1 / (ranking.index(target) + 1) if target in ranking else 0.0)
    strata = Counter(
        f"{key_states[s]['championId']}:{key_states[s]['mainItemOrdinal']}" for s in states
    )
    return {
        "schemaVersion": 1,
        "rubric": key.get("rubric"),
        "seed": seed,
        "synthetic": bool(key.get("synthetic")),
        "sample": {
            "states": len(states),
            "items": sum(len(key_states[s]["items"]) for s in states),
            "strata": dict(sorted(strata.items())),
            "reviewers": len(reviewers),
        },
        "reviewers": per_reviewer,
        "agreement": agreement,
        "pertinence": pertinence,
        "preferences": {
            "popularityTop1_vs_historical": preference("popularity_top1", "historical"),
            "modelTop1_vs_popularityTop1": preference("model_top1", "popularity_top1"),
            "historical_vs_control": preference("historical", "control"),
        },
        "imitation": {
            "popularityTop1IsHistorical": round(sum(hits) / len(hits), 4) if hits else None,
            "popularityMrrTop10": round(sum(reciprocal) / len(reciprocal), 4)
            if reciprocal
            else None,
        },
    }


class ReviewError(Exception):
    """Insumo divergente do congelado, ou saída já existente."""


def sample_from_run(
    dataset_path: Any,
    run_dir: Any,
    champion_path: Any,
    item_path: Any,
    names_path: Any,
    out_sheet: Any,
    out_key: Any,
    model_name: str = "M1-full",
    splits: Sequence[str] = ("test",),
    ordinals: Sequence[int] | None = None,
    per_stratum: int = PER_STRATUM,
) -> dict[str, Any]:
    """Monta folha e chave com o dataset, os catálogos e o modelo da execução congelada.

    Recusa insumo cujo hash difere do registrado na execução e nunca sobrescreve saída.
    """
    import json
    from pathlib import Path

    from catboost import CatBoostRanker

    from riftlab.dataset.popularity import fit
    from riftlab.ml.experiment import _load, _sha, _write_once
    from riftlab.ml.ranker import b0_scores, model_scores

    run_dir, out_sheet, out_key = Path(run_dir), Path(out_sheet), Path(out_key)
    if out_sheet.exists() or out_key.exists():
        raise ReviewError("folha ou chave já existe; nunca sobrescrita")
    run = json.loads((run_dir / "run.json").read_bytes())
    for field, path in (
        ("datasetSha256", dataset_path),
        ("championCatalogSha256", champion_path),
        ("itemCatalogSha256", item_path),
    ):
        if _sha(Path(path)) != run["inputs"][field]:
            raise ReviewError(f"{field} diverge da execução congelada")
    meta = run["models"][model_name]
    if _sha(run_dir / meta["file"]) != meta["sha256"]:
        raise ReviewError("modelo alterado depois da seleção")
    rows, catalogs = _load(Path(dataset_path), Path(champion_path), Path(item_path))
    model = CatBoostRanker()
    model.load_model(str(run_dir / meta["file"]))
    champions = json.loads(Path(champion_path).read_bytes())["data"]
    items_en = json.loads(Path(item_path).read_bytes())["data"]
    names_pt = json.loads(Path(names_path).read_bytes())["data"]
    sheet, key = build_sample(
        rows,
        b0_scores(fit([r for r in rows if r["split"] == "train"])),
        model_scores(model, catalogs, frozenset(meta["dropped"])),
        {int(c["key"]): str(c["name"]) for c in champions.values()},
        {int(i): str(e["name"]) for i, e in names_pt.items()},
        {int(i): int(e["gold"]["total"]) for i, e in items_en.items()},
        per_stratum=per_stratum,
        splits=splits,
        ordinals=ordinals,
    )
    key["selection"] = {"splits": list(splits), "ordinals": ordinals, "perStratum": per_stratum}
    key["inputs"] = {
        "datasetSha256": run["inputs"]["datasetSha256"],
        "runSha256": _sha(run_dir / "run.json"),
        "model": model_name,
        "modelSha256": meta["sha256"],
        "itemNamesSha256": _sha(Path(names_path)),
    }
    sheet_sha = _write_once(out_sheet, sheet)
    key_sha = _write_once(out_key, key, mode=0o400)
    return {"states": len(sheet["states"]), "sheetSha256": sheet_sha, "keySha256": key_sha}


# ---------------------------------------------------------------------------
# Versão por escolha (13/10/2026): o formulário por nota 0–3 se mostrou longo demais para
# os revisores. Cada pessoa responde um bloco (~26 situações): escolhe o item que compraria
# (ou "não sei") e, se quiser, marca os que não fazem sentido. Situações âncora, iguais em
# todos os blocos, medem a concordância entre todos.
# ---------------------------------------------------------------------------

ANCHORS = 0  # blocos disjuntos; a concordância é medida entre quem responde o mesmo bloco
BLOCKS = 3
TOTAL = 60  # 3 blocos de 20 (decisão do usuário)


def trim_sample(key: Mapping[str, Any], total: int = TOTAL, seed: int = REVIEW_SEED) -> list[str]:
    """Reduz a amostra a `total` tirando, um por vez, a última situação (por hash) do maior
    estrato, para manter campeões e ordens do item o mais equilibrados possível."""
    by_stratum: dict[str, list[str]] = defaultdict(list)
    for state_id in sorted(key["states"], key=lambda s: _digest(seed, "corte", s)):
        meta = key["states"][state_id]
        by_stratum[f"{meta['championId']}:{meta['mainItemOrdinal']}"].append(state_id)
    while sum(len(v) for v in by_stratum.values()) > total:
        largest = max(sorted(by_stratum), key=lambda stratum: len(by_stratum[stratum]))
        by_stratum[largest].pop()
    return sorted(s for v in by_stratum.values() for s in v)


def plan_blocks(
    key: Mapping[str, Any],
    blocks: int = BLOCKS,
    anchors: int = ANCHORS,
    seed: int = REVIEW_SEED,
    keep: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Âncoras estratificadas (uma por estrato, depois por hash) e o resto distribuído por estrato
    entre os blocos, para cada bloco ter a mesma mistura de campeão e ordem do item. `keep`
    restringe a amostra (ver `trim_sample`)."""
    states = key["states"]
    selected = list(keep) if keep is not None else list(states)
    by_stratum: dict[str, list[str]] = defaultdict(list)
    for state_id in sorted(selected, key=lambda s: _digest(seed, "bloco", s)):
        meta = states[state_id]
        by_stratum[f"{meta['championId']}:{meta['mainItemOrdinal']}"].append(state_id)
    chosen: list[str] = []
    while len(chosen) < anchors and any(by_stratum.values()):
        for stratum in sorted(by_stratum):
            if by_stratum[stratum] and len(chosen) < anchors:
                chosen.append(by_stratum[stratum].pop(0))
    rest = [s for stratum in sorted(by_stratum) for s in by_stratum[stratum]]
    assigned: dict[str, list[str]] = {str(b + 1): [] for b in range(blocks)}
    for index, state_id in enumerate(rest):
        assigned[str(index % blocks + 1)].append(state_id)

    def shuffled(block: str, ids: list[str]) -> list[str]:
        return sorted(ids, key=lambda state_id: _digest(seed, "ordem", block, state_id))

    order = {block: shuffled(block, chosen + own) for block, own in assigned.items()}
    return {"seed": seed, "anchors": sorted(chosen), "blocks": order}


def fleiss_kappa(table: Sequence[Sequence[int]]) -> float | None:
    """Kappa de Fleiss; cada linha conta quantos avaliadores puseram o sujeito em cada categoria.
    Linhas com menos de 2 avaliadores são ignoradas; avaliadores por linha podem variar."""
    rows = [list(r) for r in table if sum(r) >= 2]
    if not rows:
        return None
    categories = max(len(r) for r in rows)
    rows = [r + [0] * (categories - len(r)) for r in rows]
    agreement = []
    totals = [0.0] * categories
    weight = 0
    for r in rows:
        n = sum(r)
        agreement.append((sum(c * c for c in r) - n) / (n * (n - 1)))
        for j, c in enumerate(r):
            totals[j] += c
        weight += n
    p_bar = sum(agreement) / len(agreement)
    p_e = sum((t / weight) ** 2 for t in totals)
    return None if p_e == 1 else (p_bar - p_e) / (1 - p_e)


def analyze_choices(
    key: Mapping[str, Any],
    responses: Mapping[str, Mapping[str, Any]],
    plan: Mapping[str, Any],
    resamples: int = RESAMPLES,
    seed: int = REVIEW_SEED,
) -> dict[str, Any]:
    """Agregado sem identidade da revisão por escolha.

    `responses`: {pseudônimo: {"block": "1", "profile": {...}, "choices": {estado: itemId|None},
    "rejected": {estado: [itemIds]}}}. Origem da escolha, rejeição por origem, concordância
    (Fleiss nas âncoras; acordo par a par) e imitação na mesma amostra.
    """
    key_states = key["states"]
    raters = sorted(responses)
    states = sorted({s for r in raters for s in (responses[r].get("choices") or {})})

    def roles_of(state: str, item: str | None) -> set[str]:
        if item is None:
            return {"unsure"}
        meta = key_states[state]["items"].get(str(item))
        if meta is None:
            return set()
        found = set()
        if meta["popularityRank"] == 1:
            found.add("popularity_top1")
        if meta["popularityRank"] is not None:
            found.add("popularity_top3")
        if meta["modelTop1"]:
            found.add("model_top1")
        if meta["historical"]:
            found.add("historical")
        if meta["control"]:
            found.add("control")
        return found

    picks = [
        (state, r, responses[r]["choices"][state])
        for r in raters
        for state in (responses[r].get("choices") or {})
    ]

    def share(selected: Sequence[str], role: str) -> float | None:
        chosen = [p for p in picks if p[0] in set(selected)]
        if not chosen:
            return None
        return sum(role in roles_of(state, item) for state, _, item in chosen) / len(chosen)

    def share_statistic(role: str) -> Callable[[Sequence[str]], float | None]:
        return lambda selected: share(selected, role)

    chosen_by = {
        role: {
            "share": None if (v := share(states, role)) is None else round(v, 3),
            "ci95": _bootstrap(states, share_statistic(role), resamples, seed),
        }
        for role in (*ROLES, "unsure")
    }

    rejections: dict[str, list[int]] = {role: [0, 0] for role in ROLES}
    for r in raters:
        rejected = responses[r].get("rejected") or {}
        for state in responses[r].get("choices") or {}:
            marked = {str(i) for i in rejected.get(state, [])}
            for item in key_states[state]["items"]:
                for role in roles_of(state, item):
                    if role in rejections:
                        rejections[role][1] += 1
                        rejections[role][0] += item in marked
    rejected_by = {
        role: {"rate": round(a / b, 3) if b else None, "n": b}
        for role, (a, b) in rejections.items()
    }

    def table(selected: Sequence[str]) -> list[list[int]]:
        rows = []
        for state in selected:
            options = [*key_states[state]["items"], None]
            counts = [0] * len(options)
            for r in raters:
                if state in (responses[r].get("choices") or {}):
                    value = responses[r]["choices"][state]
                    counts[options.index(None if value is None else str(value))] += 1
            rows.append(counts)
        return rows

    anchors = [s for s in plan["anchors"] if s in states]
    pair_hits = pair_total = 0
    for state in states:
        votes = [
            responses[r]["choices"][state]
            for r in raters
            if state in (responses[r].get("choices") or {})
        ]
        for i in range(len(votes)):
            for j in range(i + 1, len(votes)):
                pair_total += 1
                pair_hits += votes[i] == votes[j]

    def kappa_of(selected: Sequence[str]) -> float | None:
        return fleiss_kappa(table(selected))

    kappa_anchor = fleiss_kappa(table(anchors))
    kappa_all = fleiss_kappa(table(states))
    hits = [key_states[s]["popularityRanking"][0] == key_states[s]["targetItemId"] for s in states]
    return {
        "schemaVersion": 1,
        "task": "choice_v1",
        "seed": seed,
        "synthetic": bool(key.get("synthetic")),
        "sample": {
            "states": len(states),
            "raters": len(raters),
            "answers": len(picks),
            "anchors": len(anchors),
            "raterBlocks": dict(
                sorted(Counter(str(responses[r].get("block")) for r in raters).items())
            ),
        },
        "raters": [
            {
                "ref": f"R{index + 1}",
                "block": responses[r].get("block"),
                "experience": (responses[r].get("profile") or {}).get("experience"),
                "answered": len(responses[r].get("choices") or {}),
                "unsure": sum(v is None for v in (responses[r].get("choices") or {}).values()),
            }
            for index, r in enumerate(raters)
        ],
        "agreement": {
            "fleissAnchors": None if kappa_anchor is None else round(kappa_anchor, 4),
            "fleissAll": None if kappa_all is None else round(kappa_all, 4),
            "ci95All": _bootstrap(states, kappa_of, resamples, seed),
            "pairwiseExact": round(pair_hits / pair_total, 4) if pair_total else None,
            "pairs": pair_total,
        },
        "chosenBy": chosen_by,
        "rejectedBy": rejected_by,
        "imitation": {
            "popularityTop1IsHistorical": round(sum(hits) / len(hits), 4) if hits else None,
        },
    }
