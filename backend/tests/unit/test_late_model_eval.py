"""CC-1503: padrão hierárquico, conjunto plausível, bloco de features e métricas de desvio."""

from __future__ import annotations

import numpy as np
import pytest

from riftlab.ml.late_eval import Scored, deviation_item, evaluate, per_decision
from riftlab.ml.late_model import FeatureSpace, LatePrior, decision_block, plausible
from riftlab.profiles.domain import ItemProfile


def row(
    champion: int,
    target: int,
    split: str = "train",
    role: str = "TOP",
    ordinal: int = 5,
    candidates=(10, 20, 30),
    match: str = "M1",
    context=None,
):
    return {
        "matchId": match,
        "split": split,
        "championId": champion,
        "role": role,
        "ordinal": ordinal,
        "targetItemId": target,
        "targetLegal": True,
        "candidates": [
            {"itemId": i, "remainingCost": 3000, "ownedComponent": False} for i in candidates
        ],
        "context": context
        or {"minute": 30.0, "enemy_healers": 2.0, "self_champion_id": str(champion)},
        "completion": {"kind": "FORCED"},
    }


def test_prior_backs_off_from_champion_to_role_to_global_and_renormalizes():
    rows = [row(1, 10)] * 8 + [row(2, 20, role="TOP")] * 30 + [row(3, 30, role="MIDDLE")] * 50
    prior = LatePrior.fit(rows)
    own = prior.probabilities(1, "TOP", 5, [10, 20, 30])
    assert own.sum() == pytest.approx(1.0) and own.argmax() == 0  # o próprio campeão manda
    rare = prior.probabilities(99, "TOP", 5, [10, 20, 30])
    assert rare.argmax() == 1  # sem histórico: o padrão da rota
    nowhere = prior.probabilities(99, "SUPPORT", 5, [10, 20, 30])
    assert nowhere.argmax() == 2  # sem rota: o geral
    with pytest.raises(ValueError):
        LatePrior.fit([row(1, 10, split="validation")])


def test_plausible_set_and_feature_block_shapes():
    prior = LatePrior.fit([row(1, 10)] * 5 + [row(1, 20)] * 2)
    ids, q = plausible(row(1, 10), prior, top_k=2)
    assert ids == [10, 20] and q.sum() == pytest.approx(1.0)
    profiles = {
        10: ItemProfile(10, {"armor": 50.0}, frozenset({"ANTIHEAL"})),
        20: ItemProfile(20, {}, frozenset()),
    }
    space = FeatureSpace.build(["enemy_healers", "minute"], profiles, {10: 3000, 20: 3100})
    numeric, categorical = decision_block(row(1, 10), ids, q, space)
    assert numeric.shape == (2, len(space.numeric_names))
    assert categorical.shape == (2, len(space.categorical_names))
    antiheal = space.numeric_names.index("m_antiheal_vs_sustain")
    assert numeric[0, antiheal] == pytest.approx(2.0) and numeric[1, antiheal] == 0.0


def test_deviation_metrics_and_claim():
    base = np.array([0.6, 0.3, 0.1])
    model = np.array([0.3, 0.2, 0.5])
    hit = Scored(row(1, 30, match="A"), [10, 20, 30], base, model)
    assert deviation_item(hit) == 30  # lift log(5) e P 0,5
    metrics = per_decision(hit)
    assert metrics["base_hit1"] == 0.0 and metrics["model_hit1"] == 1.0
    assert metrics["model_nll"] < metrics["base_nll"]
    miss = Scored(row(1, 10, match="B"), [10, 20, 30], base, base)
    assert deviation_item(miss) is None
    report = evaluate([hit, miss], resamples=False)
    assert report["deviation"]["decisions"] == 1
    assert (
        report["deviationClaims"]["shown"] == 1 and report["deviationClaims"]["observedRate"] == 1.0
    )
