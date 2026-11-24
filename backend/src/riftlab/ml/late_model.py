"""Modelo de late game (CC-1503): padrão hierárquico + modelo contextual + lift.

- `LatePrior` é o padrão: frequência do item como k-ésimo item principal, com encolhimento do
  campeão para a rota e da rota para o geral (qualquer campeão tem padrão, mesmo raro), e
  renormalizada sobre os candidatos legais daquela decisão.
- O conjunto plausível são os `top_k` candidatos legais pelo padrão (geração de candidatos); o
  modelo só reordena dentro dele.
- `LateModel` (CatBoost, softmax por decisão) estima P(item | contexto). Recebe o log do padrão
  como feature, então aprende o desvio que o contexto provoca.
- `lift = log P(contexto) − log P(padrão)`: o item que a partida pede mais do que o normal.

Vetorizado em numpy: uma decisão vira um bloco (candidatos × features) sem laço por célula.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from riftlab.profiles.domain import ITEM_STATS, ItemProfile

LATE_MODEL_VERSION = "late_context_v3"  # v3: patch e elo como categóricas
# Features categóricas do contexto (`ml/situation.py` as produz como texto). Ficam aqui para o
# treino não importar a camada de banco (o pacote do Colab só leva catboost/numpy/typer/pyyaml).
CATEGORICAL_CONTEXT = ("self_champion_id", "self_role", "self_primary_tag", "top_enemy_tag")
# Categóricas da linha (não da situação): patch e faixa de elo do corpus. Num treino com mais de
# um patch, o modelo separa o que é meta do patch e o que é elo do que a partida pede. Ao servir,
# vêm de `rowDefaults` do artefato (patch servido, elo de referência).
ROW_CATEGORICAL = ("patch", "elo")
MIXED_ELO = "MIXED"  # corpus sem seleção de elo (ex.: 16.19)
PRIOR_VERSION = "late_prior_v1"
DEFAULT_TOP_K = 30
ITEM_MECHANIC_FEATURES = (
    "ANTIHEAL",
    "STASIS",
    "SPELL_SHIELD",
    "REVIVE",
    "CLEANSE",
    "SHIELD",
    "ANTI_SHIELD",
    "SLOW",
    "ARMOR_SHRED",
    "MR_SHRED",
    "EXECUTE",
    "ANTI_ATTACK_SPEED",
    "MAGIC_SHIELD",
)
# Peso das decisões por ordem: o foco do late game é o 5º e o 6º item.
ORDINAL_WEIGHTS = {3: 1.0, 4: 1.25, 5: 2.0, 6: 2.0}


def elo_group(tier: str | None) -> str:
    """Faixa de elo da semente que trouxe a partida (manifesto da coleta de elo alto)."""
    if not tier:
        return MIXED_ELO
    if tier.startswith("DIAMOND"):
        return "DIAMOND"
    if tier in {
        "CHALLENGER",
        "GRANDMASTER",
        "MASTER",
        "CHALLENGER_OR_GRANDMASTER",
        "MASTER_OR_GRANDMASTER",
    }:
        return "MASTER_PLUS"
    return MIXED_ELO


# ---------------------------------------------------------------- padrão hierárquico


@dataclass
class LatePrior:
    """Contagens de alvo por (ordem), (rota, ordem) e (campeão, ordem), só do treino."""

    global_counts: dict[int, Counter[int]] = field(default_factory=lambda: defaultdict(Counter))
    role_counts: dict[tuple[str, int], Counter[int]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    champion_counts: dict[tuple[int, int], Counter[int]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    role_strength: float = 20.0
    champion_strength: float = 10.0
    smoothing: float = 0.5
    version: str = PRIOR_VERSION

    @classmethod
    def fit(cls, rows: Iterable[Mapping[str, Any]]) -> LatePrior:
        prior = cls()
        for row in rows:
            if row["split"] != "train":
                raise ValueError("o padrão só pode ver o treino")
            if not row.get("targetLegal"):
                continue
            k, item = int(row["ordinal"]), int(row["targetItemId"])
            prior.global_counts[k][item] += 1
            prior.role_counts[(str(row.get("role") or "NONE"), k)][item] += 1
            prior.champion_counts[(int(row["championId"]), k)][item] += 1
        return prior

    def probabilities(
        self, champion_id: int, role: str | None, ordinal: int, item_ids: Sequence[int]
    ) -> np.ndarray:
        """P(item) do padrão, renormalizada sobre `item_ids` (os candidatos legais)."""
        g = self.global_counts.get(ordinal, Counter())
        r = self.role_counts.get((role or "NONE", ordinal), Counter())
        c = self.champion_counts.get((champion_id, ordinal), Counter())
        gn, rn, cn = sum(g.values()), sum(r.values()), sum(c.values())
        n = len(item_ids) or 1
        out = np.empty(len(item_ids), dtype=np.float64)
        for i, item in enumerate(item_ids):
            p0 = (g[item] + self.smoothing) / (gn + self.smoothing * n)
            p1 = (r[item] + self.role_strength * p0) / (rn + self.role_strength)
            out[i] = (c[item] + self.champion_strength * p1) / (cn + self.champion_strength)
        total = out.sum()
        return out / total if total > 0 else np.full(len(item_ids), 1.0 / n)

    def support(self, champion_id: int, ordinal: int) -> int:
        return sum(self.champion_counts.get((champion_id, ordinal), Counter()).values())

    def to_document(self) -> dict[str, Any]:
        def dump(table: Mapping[Any, Counter[int]]) -> list[Any]:
            return [[list(k) if isinstance(k, tuple) else k, dict(v)] for k, v in table.items()]

        return {
            "version": self.version,
            "roleStrength": self.role_strength,
            "championStrength": self.champion_strength,
            "smoothing": self.smoothing,
            "global": dump(self.global_counts),
            "role": dump(self.role_counts),
            "champion": dump(self.champion_counts),
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> LatePrior:
        prior = cls(
            role_strength=float(document["roleStrength"]),
            champion_strength=float(document["championStrength"]),
            smoothing=float(document["smoothing"]),
            version=document["version"],
        )
        for k, counts in document["global"]:
            prior.global_counts[int(k)] = Counter({int(i): n for i, n in counts.items()})
        for (role, k), counts in document["role"]:
            prior.role_counts[(role, int(k))] = Counter({int(i): n for i, n in counts.items()})
        for (champion, k), counts in document["champion"]:
            prior.champion_counts[(int(champion), int(k))] = Counter(
                {int(i): n for i, n in counts.items()}
            )
        return prior


# ---------------------------------------------------------------- features


CANDIDATE_NUMERIC = (
    *(f"cand_{s}" for s in ITEM_STATS),
    *(f"cand_{m.lower()}" for m in ITEM_MECHANIC_FEATURES),
    "cand_price",
    "cand_remaining_cost",
    "cand_owned_component",
    "prior_log",
    "prior_rank",
)
MATCHUP = (
    "m_armor_vs_physical",
    "m_mr_vs_magic",
    "m_antiheal_vs_sustain",
    "m_antiheal_already_team",
    "m_armorpen_vs_armor",
    "m_magicpen_vs_mr",
    "m_stasis_vs_threat",
    "m_spellshield_vs_cc",
    "m_cleanse_vs_cc",
    "m_revive_vs_deaths",
    "m_antishield_vs_shields",
    "m_anti_as_vs_attackers",
    "m_ap_fit",
    "m_ad_fit",
    "m_hp_vs_burst",
)
CANDIDATE_CATEGORICAL = ("cand_item_id",)


def context_columns(example_context: Mapping[str, Any]) -> tuple[str, ...]:
    """Colunas numéricas de contexto, em ordem estável (as categóricas ficam à parte)."""
    return tuple(sorted(k for k in example_context if k not in CATEGORICAL_CONTEXT))


@dataclass(frozen=True)
class FeatureSpace:
    context_numeric: tuple[str, ...]
    item_index: Mapping[int, int]
    item_matrix: np.ndarray  # itens × (atributos + mecânicas + preço)
    # Artefatos antigos não guardavam a lista: valem as categóricas da situação.
    categorical_context: tuple[str, ...] = CATEGORICAL_CONTEXT

    @property
    def numeric_names(self) -> tuple[str, ...]:
        return (*self.context_numeric, *CANDIDATE_NUMERIC, *MATCHUP)

    @property
    def categorical_names(self) -> tuple[str, ...]:
        return (*self.categorical_context, *CANDIDATE_CATEGORICAL)

    @classmethod
    def build(
        cls,
        context_numeric: Sequence[str],
        profiles: Mapping[int, ItemProfile],
        prices: Mapping[int, int],
        categorical_context: Sequence[str] = CATEGORICAL_CONTEXT,
    ) -> FeatureSpace:
        ids = sorted(set(profiles) | set(prices))
        width = len(ITEM_STATS) + len(ITEM_MECHANIC_FEATURES) + 1
        matrix = np.full((len(ids) + 1, width), np.nan, dtype=np.float32)  # última linha: ausente
        for row, item in enumerate(ids):
            profile = profiles.get(item)
            if profile is not None:
                matrix[row, : len(ITEM_STATS)] = [profile.stat(s) for s in ITEM_STATS]
                matrix[row, len(ITEM_STATS) : -1] = [
                    float(m in profile.mechanics) for m in ITEM_MECHANIC_FEATURES
                ]
            if item in prices:
                matrix[row, -1] = prices[item]
        return cls(
            tuple(context_numeric),
            {item: i for i, item in enumerate(ids)},
            matrix,
            tuple(categorical_context),
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "contextNumeric": list(self.context_numeric),
            "categoricalContext": list(self.categorical_context),
        }


def _ctx(context: Mapping[str, Any], name: str) -> float:
    value = context.get(name)
    if isinstance(value, bool):
        return float(value)
    return float(value) if isinstance(value, (int, float)) else math.nan


def decision_block(
    row: Mapping[str, Any],
    candidate_ids: Sequence[int],
    prior_p: np.ndarray,
    space: FeatureSpace,
) -> tuple[np.ndarray, np.ndarray]:
    """(numéricas float32 candidatos × N, categóricas object candidatos × C) de uma decisão."""
    context = row["context"]
    k = len(candidate_ids)
    by_candidate = {int(c["itemId"]): c for c in row["candidates"]}
    ctx = np.array([_ctx(context, n) for n in space.context_numeric], dtype=np.float32)
    rows_idx = [space.item_index.get(i, len(space.item_matrix) - 1) for i in candidate_ids]
    items = space.item_matrix[rows_idx]
    stats = {s: items[:, j] for j, s in enumerate(ITEM_STATS)}
    mech = {m: items[:, len(ITEM_STATS) + j] for j, m in enumerate(ITEM_MECHANIC_FEATURES)}
    remaining = np.array(
        [float(by_candidate[i]["remainingCost"]) for i in candidate_ids], dtype=np.float32
    )
    owned = np.array(
        [float(by_candidate[i].get("ownedComponent", False)) for i in candidate_ids],
        dtype=np.float32,
    )
    prior_log = np.log(np.clip(prior_p, 1e-9, 1.0)).astype(np.float32)
    prior_rank = (np.argsort(np.argsort(-prior_p)) + 1).astype(np.float32)

    def c(name: str) -> float:
        value = _ctx(context, name)
        return 0.0 if math.isnan(value) else value

    magic_share = _ctx(context, "enemy_magic_share")
    magic_share = 0.5 if math.isnan(magic_share) else magic_share
    self_magic = _ctx(context, "self_magic_share")
    self_magic = 0.5 if math.isnan(self_magic) else self_magic
    nz = lambda a: np.nan_to_num(a, nan=0.0)  # noqa: E731
    matchup = np.stack(
        [
            nz(stats["armor"]) * (1 - magic_share) * (1 + c("enemy_items_ad") / 300),
            nz(stats["mr"]) * magic_share * (1 + c("enemy_items_ap") / 300),
            nz(mech["ANTIHEAL"]) * (c("enemy_healers") + c("enemy_items_sustain") / 10),
            nz(mech["ANTIHEAL"]) * (c("ally_items_antiheal") + c("self_items_antiheal")),
            (nz(stats["armor_pen"]) + nz(stats["lethality"]) / 2) * c("enemy_items_armor") / 100,
            (nz(stats["magic_pen_pct"]) + nz(stats["magic_pen"])) * c("enemy_items_mr") / 100,
            nz(mech["STASIS"]) * (c("deaths_to_assassin") + c("enemy_assassin")),
            nz(mech["SPELL_SHIELD"]) * c("enemy_hard_cc"),
            nz(mech["CLEANSE"]) * c("enemy_hard_cc"),
            nz(mech["REVIVE"]) * c("self_deaths"),
            nz(mech["ANTI_SHIELD"]) * (c("enemy_shielders") + c("enemy_items_shield")),
            nz(mech["ANTI_ATTACK_SPEED"])
            * (c("enemy_marksman") + c("enemy_items_attack_speed") / 40),
            nz(stats["ap"]) * self_magic,
            nz(stats["ad"]) * (1 - self_magic),
            nz(stats["hp"]) * (c("deaths_to_assassin") + c("top_enemy_kills") / 3),
        ],
        axis=1,
    ).astype(np.float32)
    numeric = np.hstack(
        [
            np.broadcast_to(ctx, (k, len(ctx))),
            items,
            remaining[:, None],
            owned[:, None],
            prior_log[:, None],
            prior_rank[:, None],
            matchup,
        ]
    ).astype(np.float32)
    categorical = np.empty((k, len(space.categorical_context) + 1), dtype=object)
    for j, name in enumerate(space.categorical_context):
        value = row.get(name) if name in ROW_CATEGORICAL else context.get(name)
        categorical[:, j] = "NONE" if value is None else str(value)
    categorical[:, -1] = [str(i) for i in candidate_ids]
    return numeric, categorical


def plausible(row: Mapping[str, Any], prior: LatePrior, top_k: int) -> tuple[list[int], np.ndarray]:
    """Os `top_k` candidatos legais pelo padrão (empate pelo id) e o padrão renormalizado neles."""
    ids = [int(c["itemId"]) for c in row["candidates"]]
    p = prior.probabilities(int(row["championId"]), row.get("role"), int(row["ordinal"]), ids)
    order = sorted(range(len(ids)), key=lambda i: (-p[i], ids[i]))[:top_k]
    chosen = [ids[i] for i in order]
    q = p[order]
    return chosen, q / q.sum()


@dataclass
class Matrix:
    numeric: np.ndarray
    categorical: np.ndarray
    labels: np.ndarray
    groups: np.ndarray
    group_weights: np.ndarray
    decisions: list[tuple[int, list[int], np.ndarray]]  # (índice da linha, candidatos, padrão)


def build_matrix(
    rows: Sequence[Mapping[str, Any]],
    prior: LatePrior,
    space: FeatureSpace,
    top_k: int,
    *,
    require_target: bool,
) -> Matrix:
    """Matriz empilhada. `require_target=True` (treino) descarta decisões cujo alvo ficou fora
    do conjunto plausível; na avaliação elas ficam e contam como erro."""
    numeric_blocks: list[np.ndarray] = []
    categorical_blocks: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    groups: list[np.ndarray] = []
    weights: list[float] = []
    decisions: list[tuple[int, list[int], np.ndarray]] = []
    for index, row in enumerate(rows):
        if not row.get("candidates"):
            continue
        ids, q = plausible(row, prior, top_k)
        target = int(row["targetItemId"])
        if require_target and target not in ids:
            continue
        numeric, categorical = decision_block(row, ids, q, space)
        numeric_blocks.append(numeric)
        categorical_blocks.append(categorical)
        labels.append(np.array([float(i == target) for i in ids], dtype=np.float32))
        groups.append(np.full(len(ids), len(decisions), dtype=np.int64))
        weights.append(ORDINAL_WEIGHTS.get(int(row["ordinal"]), 1.0))
        decisions.append((index, ids, q))
    return Matrix(
        np.vstack(numeric_blocks) if numeric_blocks else np.empty((0, len(space.numeric_names))),
        np.vstack(categorical_blocks)
        if categorical_blocks
        else np.empty((0, len(space.categorical_names)), dtype=object),
        np.concatenate(labels) if labels else np.empty(0),
        np.concatenate(groups) if groups else np.empty(0, dtype=np.int64),
        np.array(weights, dtype=np.float32),
        decisions,
    )


def make_pool(matrix: Matrix, space: FeatureSpace, *, with_labels: bool = True) -> Any:
    from catboost import FeaturesData, Pool

    data = FeaturesData(
        num_feature_data=np.ascontiguousarray(matrix.numeric, dtype=np.float32),
        cat_feature_data=matrix.categorical,
        num_feature_names=list(space.numeric_names),
        cat_feature_names=list(space.categorical_names),
    )
    if not with_labels:
        return Pool(data)
    group_weight = matrix.group_weights[matrix.groups] if len(matrix.groups) else None
    return Pool(data, label=matrix.labels, group_id=matrix.groups, group_weight=group_weight)


def softmax_by_group(scores: np.ndarray, sizes: Sequence[int]) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    start = 0
    for size in sizes:
        s = scores[start : start + size].astype(np.float64)
        e = np.exp(s - s.max())
        out.append(e / e.sum())
        start += size
    return out


# ---------------------------------------------------------------- artefato servido


@dataclass
class LateArtifact:
    """Modelo + padrão + espaço de features + metadados, carregado do diretório do artefato."""

    model: Any | None
    prior: LatePrior
    space: FeatureSpace
    top_k: int
    metadata: Mapping[str, Any]

    def _with_defaults(self, row: Mapping[str, Any]) -> Mapping[str, Any]:
        """Patch e elo que a linha servida não traz: os de referência do artefato."""
        defaults = self.metadata.get("rowDefaults") or {}
        missing = {k: v for k, v in defaults.items() if row.get(k) is None}
        return {**row, **missing} if missing else row

    def score(self, row: Mapping[str, Any]) -> tuple[list[int], np.ndarray, np.ndarray]:
        """(candidatos plausíveis, P padrão, P contexto). Sem modelo, contexto = padrão."""
        row = self._with_defaults(row)
        ids, q = plausible(row, self.prior, self.top_k)
        if self.model is None or not ids:
            return ids, q, q
        numeric, categorical = decision_block(row, ids, q, self.space)
        matrix = Matrix(
            numeric,
            categorical,
            np.zeros(len(ids)),
            np.zeros(len(ids), np.int64),
            np.ones(1, np.float32),
            [],
        )
        raw = np.asarray(self.model.predict(make_pool(matrix, self.space, with_labels=False)))
        return ids, q, softmax_by_group(raw, [len(ids)])[0]

    def contributions(self, row: Mapping[str, Any]) -> tuple[list[int], np.ndarray]:
        """Valores SHAP (candidatos × features numéricas+categóricas) da decisão."""
        row = self._with_defaults(row)
        ids, q = plausible(row, self.prior, self.top_k)
        numeric, categorical = decision_block(row, ids, q, self.space)
        matrix = Matrix(
            numeric,
            categorical,
            np.zeros(len(ids)),
            np.zeros(len(ids), np.int64),
            np.ones(1, np.float32),
            [],
        )
        pool = make_pool(matrix, self.space, with_labels=False)
        assert self.model is not None
        shap = np.asarray(self.model.get_feature_importance(data=pool, type="ShapValues"))
        return ids, shap[:, :-1]  # última coluna: valor esperado

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        if self.model is not None:
            self.model.save_model(str(directory / "model.cbm"))
        (directory / "prior.json").write_text(json.dumps(self.prior.to_document()))
        (directory / "metadata.json").write_text(
            json.dumps(
                {**self.metadata, "topK": self.top_k, "space": self.space.to_document()},
                indent=1,
                sort_keys=True,
            )
        )

    @classmethod
    def load(
        cls,
        directory: Path,
        profiles: Mapping[int, ItemProfile],
        prices: Mapping[int, int],
    ) -> LateArtifact:
        metadata = json.loads((directory / "metadata.json").read_text())
        prior = LatePrior.from_document(json.loads((directory / "prior.json").read_text()))
        space = FeatureSpace.build(
            metadata["space"]["contextNumeric"],
            profiles,
            prices,
            metadata["space"].get("categoricalContext", CATEGORICAL_CONTEXT),
        )
        model = None
        if (directory / "model.cbm").exists():
            from catboost import CatBoost

            model = CatBoost()
            model.load_model(str(directory / "model.cbm"))
        return cls(model, prior, space, int(metadata["topK"]), metadata)


__all__ = [
    "CATEGORICAL_CONTEXT",
    "DEFAULT_TOP_K",
    "LATE_MODEL_VERSION",
    "MATCHUP",
    "MIXED_ELO",
    "ORDINAL_WEIGHTS",
    "ROW_CATEGORICAL",
    "FeatureSpace",
    "LateArtifact",
    "LatePrior",
    "Matrix",
    "build_matrix",
    "context_columns",
    "decision_block",
    "elo_group",
    "make_pool",
    "plausible",
    "softmax_by_group",
]
