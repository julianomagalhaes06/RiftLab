"""CC-303/304/305: library-free ranking engine over LEGAL candidates only.

Engines: POPULARITY (aggregate counts per champion × main item ordinal, from the
training split) and RULES (explicit, versioned weights). The objective applies an
explicit, versioned reordering on top of the base order, which is preserved for
audit. Scores are relative within one decision, never probabilities. Each
explanation sentence names the real field and version it comes from; missing
data produces no sentence.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

POPULARITY, RULES = "POPULARITY", "RULES"


@dataclass(frozen=True, slots=True)
class CandidateFacts:
    item_id: int
    name: str | None
    remaining_cost: int
    affordability: str
    evidence_codes: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DecisionFacts:
    champion_id: int
    main_item_ordinal: int | None
    enemy_physical: int | None  # inimigos com tag Marksman/Fighter/Assassin (quando conhecidos)
    enemy_magic: int | None  # inimigos com tag Mage


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    facts: CandidateFacts
    rank: int
    base_rank: int
    score: float
    explanations: tuple[dict[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Popularity:
    version: str
    cells: Mapping[str, Mapping[str, int]]
    dataset_sha256: str | None = None
    source_catalog_identity: Mapping[str, Any] | None = None
    game_patch: str | None = None

    @classmethod
    def load(cls, path: Path) -> Popularity:
        return cls.load_document(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def load_document(cls, document: Mapping[str, Any]) -> Popularity:
        return cls(
            document["version"],
            document["cells"],
            document.get("datasetSha256"),
            document.get("sourceCatalogIdentity"),
            document.get("gamePatch"),
        )

    def support(self, decision: DecisionFacts) -> int:
        return sum(
            self.cells.get(f"{decision.champion_id}|{decision.main_item_ordinal}", {}).values()
        )

    def score(self, decision: DecisionFacts, item_id: int) -> float:
        prior: dict[str, int] = {}
        for key, counts in self.cells.items():
            if key.endswith(f"|{decision.main_item_ordinal}"):
                for item, count in counts.items():
                    prior[item] = prior.get(item, 0) + count
        prior_total = sum(prior.values())
        cell = self.cells.get(f"{decision.champion_id}|{decision.main_item_ordinal}", {})
        total = sum(cell.values())
        p = prior.get(str(item_id), 0) / prior_total if prior_total else 0.0
        return (cell.get(str(item_id), 0) + p) / (total + 1)


@dataclass(frozen=True, slots=True)
class Rules:
    version: str
    owned_component: float
    remaining_cost_per_1000: float
    composition_affinity: float

    @classmethod
    def load(cls, path: Path) -> Rules:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        weights = document["weights"]
        return cls(
            document["version"],
            float(weights["ownedComponent"]),
            float(weights["remainingCostPer1000"]),
            float(weights["compositionAffinity"]),
        )

    def score(self, decision: DecisionFacts, candidate: CandidateFacts) -> float:
        value = self.owned_component * float(
            "OWNED_COMPONENT_COMPATIBLE" in candidate.evidence_codes
        )
        value += self.remaining_cost_per_1000 * candidate.remaining_cost / 1000
        enemies = (decision.enemy_physical or 0) + (decision.enemy_magic or 0)
        if enemies:
            if "Armor" in candidate.tags and decision.enemy_physical is not None:
                value += self.composition_affinity * decision.enemy_physical / enemies
            if "SpellBlock" in candidate.tags and decision.enemy_magic is not None:
                value += self.composition_affinity * decision.enemy_magic / enemies
        return value


@dataclass(frozen=True, slots=True)
class Objective:
    version: str
    weight: float
    tags: Mapping[str, tuple[str, ...]]

    @classmethod
    def load(cls, path: Path) -> Objective:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            document["version"],
            float(document["lambda"]),
            {key: tuple(value) for key, value in document["tags"].items()},
        )

    def bonus(self, priority: str, candidate: CandidateFacts) -> float:
        wanted = self.tags.get(priority, ())
        return sum(tag in candidate.tags for tag in wanted) / len(wanted) if wanted else 0.0


def _normalise(values: Sequence[float]) -> list[float]:
    low, high = min(values), max(values)
    return [0.0 if high == low else (v - low) / (high - low) for v in values]


def rank(
    decision: DecisionFacts,
    candidates: Sequence[CandidateFacts],
    *,
    engine: str,
    priority: str,
    popularity: Popularity | None,
    rules: Rules,
    objective: Objective,
    limit: int = 3,
) -> list[RankedCandidate]:
    """Base order by the engine, then explicit objective reordering; ties by item id."""
    if not candidates:
        return []
    if engine == POPULARITY:
        assert popularity is not None
        base = [popularity.score(decision, c.item_id) for c in candidates]
    else:
        base = [rules.score(decision, c) for c in candidates]
    base_order = sorted(range(len(candidates)), key=lambda i: (-base[i], candidates[i].item_id))
    base_rank = {i: position + 1 for position, i in enumerate(base_order)}
    normalised = _normalise(base)
    final = [
        normalised[i] + objective.weight * objective.bonus(priority, candidates[i])
        for i in range(len(candidates))
    ]
    order = sorted(range(len(candidates)), key=lambda i: (-final[i], candidates[i].item_id))
    ranked = []
    for position, i in enumerate(order[:limit]):
        ranked.append(
            RankedCandidate(
                candidates[i],
                position + 1,
                base_rank[i],
                round(final[i], 6),
                explain(decision, candidates[i], engine, priority, popularity, objective),
            )
        )
    return ranked


def explain(
    decision: DecisionFacts,
    candidate: CandidateFacts,
    engine: str,
    priority: str,
    popularity: Popularity | None,
    objective: Objective,
) -> tuple[dict[str, str], ...]:
    """Deterministic sentences from real fields; absent data produces no sentence."""
    out: list[dict[str, str]] = []

    def say(text: str, source: str) -> None:
        out.append({"text": text, "source": source})

    say(
        f"Faltam {candidate.remaining_cost} de ouro para completar o item.",
        "candidates.remainingCost",
    )
    if "OWNED_COMPONENT_COMPATIBLE" in candidate.evidence_codes:
        say("Aproveita componentes que você já tinha no inventário.", "candidates.evidenceCodes")
    if candidate.tags:
        say("Atributos no catálogo: " + ", ".join(candidate.tags) + ".", "catalog.items.tags")
    if engine == POPULARITY and popularity is not None:
        key = f"{decision.champion_id}|{decision.main_item_ordinal}"
        cell = popularity.cells.get(key, {})
        count, total = cell.get(str(candidate.item_id), 0), sum(cell.values())
        if total:
            say(
                f"Em {total} decisões de referência deste campeão nesta etapa da build, "
                f"este item foi a escolha em {count}.",
                f"{popularity.version}.cells",
            )
    wanted = objective.tags.get(priority, ())
    matched = [tag for tag in wanted if tag in candidate.tags]
    if matched:
        say(
            "Ganha prioridade pelo objetivo escolhido: " + ", ".join(matched) + ".",
            f"{objective.version}.tags.{priority}",
        )
    return tuple(out)


# CC-704: v2 covers the ten marksmen of the expanded scope (v1, three champions, stays for
# the history of analyses that recorded it).
POPULARITY_ARTIFACT_VERSION = "v2"


def load_default(configs: Path, patch: str) -> tuple[Popularity | None, Rules, Objective]:
    popularity_path = configs / "ranking" / f"popularity-{patch}-{POPULARITY_ARTIFACT_VERSION}.json"
    popularity = Popularity.load(popularity_path) if popularity_path.exists() else None
    return (
        popularity,
        Rules.load(configs / "ranking" / "rules-v1.yaml"),
        Objective.load(configs / "ranking" / "objective-v1.yaml"),
    )


def as_payload(item: RankedCandidate) -> dict[str, Any]:
    return {
        "itemId": item.facts.item_id,
        "name": item.facts.name,
        "rank": item.rank,
        "baseRank": item.base_rank,
        "score": item.score,
        "remainingCost": item.facts.remaining_cost,
        "affordability": item.facts.affordability,
        "evidenceCodes": list(item.facts.evidence_codes),
        "tags": list(item.facts.tags),
        "explanations": list(item.explanations),
    }
