"""CC-303/304/305 ranking engine: base order, objective reordering and explanations."""

from __future__ import annotations

from riftlab.ranking.engine import (
    POPULARITY,
    RULES,
    CandidateFacts,
    DecisionFacts,
    Objective,
    Popularity,
    Rules,
    rank,
)

RULES_V = Rules("rules_test", 2.0, -1.0, 0.5)
OBJECTIVE = Objective("objective_test", 0.3, {"BALANCED": (), "SURVIVAL": ("Armor", "Health")})
DECISION = DecisionFacts(51, 1, None, None)


def _c(item: int, cost: int, tags: tuple[str, ...] = (), owned: bool = False) -> CandidateFacts:
    evidence = ("OWNED_COMPONENT_COMPATIBLE",) if owned else ()
    return CandidateFacts(item, f"item {item}", cost, "UNKNOWN", evidence, tags)


def test_rules_prefer_owned_components_then_lower_cost() -> None:
    ranked = rank(
        DECISION,
        [_c(1, 3000), _c(2, 2000), _c(3, 2500, owned=True)],
        engine=RULES,
        priority="BALANCED",
        popularity=None,
        rules=RULES_V,
        objective=OBJECTIVE,
    )
    assert [r.facts.item_id for r in ranked] == [3, 2, 1]
    assert [r.base_rank for r in ranked] == [1, 2, 3]


def test_objective_reorders_explicitly_and_keeps_the_base_rank() -> None:
    candidates = [_c(1, 2000), _c(2, 2050, tags=("Armor", "Health")), _c(3, 3000)]
    balanced = rank(
        DECISION,
        candidates,
        engine=RULES,
        priority="BALANCED",
        popularity=None,
        rules=RULES_V,
        objective=OBJECTIVE,
    )
    survival = rank(
        DECISION,
        candidates,
        engine=RULES,
        priority="SURVIVAL",
        popularity=None,
        rules=RULES_V,
        objective=OBJECTIVE,
    )
    assert [r.facts.item_id for r in balanced] == [1, 2, 3]
    assert [r.facts.item_id for r in survival] == [2, 1, 3]
    assert survival[0].base_rank == 2  # influência do objetivo auditável
    assert any("objetivo" in e["text"] for e in survival[0].explanations)


def test_popularity_uses_the_cell_and_explains_with_counts() -> None:
    popularity = Popularity("pop_test", {"51|1": {"10": 7, "20": 3}, "81|1": {"30": 5}})
    ranked = rank(
        DECISION,
        [_c(20, 1000), _c(10, 1000), _c(30, 1000)],
        engine=POPULARITY,
        priority="BALANCED",
        popularity=popularity,
        rules=RULES_V,
        objective=OBJECTIVE,
    )
    assert [r.facts.item_id for r in ranked] == [10, 20, 30]
    assert any(
        "em 7" in e["text"] and e["source"] == "pop_test.cells" for e in ranked[0].explanations
    )


def test_explanations_never_invent_missing_data() -> None:
    ranked = rank(
        DECISION,
        [_c(1, 500)],
        engine=RULES,
        priority="BALANCED",
        popularity=None,
        rules=RULES_V,
        objective=OBJECTIVE,
    )
    texts = [e["text"] for e in ranked[0].explanations]
    assert texts == ["Faltam 500 de ouro para completar o item."]


def test_at_most_three_and_empty_input() -> None:
    many = [_c(i, 1000 + i) for i in range(1, 6)]
    assert (
        len(
            rank(
                DECISION,
                many,
                engine=RULES,
                priority="BALANCED",
                popularity=None,
                rules=RULES_V,
                objective=OBJECTIVE,
            )
        )
        == 3
    )
    assert (
        rank(
            DECISION,
            [],
            engine=RULES,
            priority="BALANCED",
            popularity=None,
            rules=RULES_V,
            objective=OBJECTIVE,
        )
        == []
    )
