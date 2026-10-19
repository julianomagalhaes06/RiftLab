"""Rótulos da revisão (CC-1003): referência por campeão × rota × etapa, prontidão e habilidades."""

from __future__ import annotations

from riftlab.match_review.reference import is_review_ready, parse_reference, skill_priority


def _reference(purchases: dict, skills: dict | None = None):  # noqa: ANN202
    return parse_reference(
        {
            "schemaVersion": "match_review_reference_v1",
            "referenceVersion": "16.19-solo-v1",
            "patch": "16.19",
            "queueId": 420,
            "source": {"dataDragonVersion": "16.19.1"},
            "purchases": purchases,
            "skills": skills or {},
        }
    )


def test_purchase_labels_follow_rank_share_and_support() -> None:
    reference = _reference(
        {
            "86:TOP:1": {
                "n": 100,
                "items": [[3078, 50], [6631, 30], [3071, 12], [3053, 5], [3742, 3]],
            },
            "86:TOP:2": {"n": 19, "items": [[3053, 19]]},
        }
    )
    assert reference.label_purchase(86, "TOP", 1, 3078)[:3] == ("COMMON", 0.5, 1)
    assert reference.label_purchase(86, "TOP", 1, 3071)[0] == "COMMON"  # 3º com 12%
    assert reference.label_purchase(86, "TOP", 1, 3053)[0] == "ALTERNATIVE"  # 4º, 5%
    assert reference.label_purchase(86, "TOP", 1, 3742)[0] == "ALTERNATIVE"  # 3% no limite
    assert reference.label_purchase(86, "TOP", 1, 9999)[:3] == ("UNCOMMON", 0.0, None)
    assert reference.label_purchase(86, "TOP", 2, 3053)[0] == "NO_REFERENCE"  # n < 20
    assert reference.label_purchase(86, "JUNGLE", 1, 3078)[0] == "NO_REFERENCE"


def test_top_three_below_ten_percent_is_an_alternative() -> None:
    reference = _reference(
        {
            "1:MIDDLE:3": {
                "n": 200,
                "items": [[1, 30], [2, 25], [3, 18]] + [[i, 7] for i in range(4, 22)],
            }
        }
    )
    assert reference.label_purchase(1, "MIDDLE", 3, 3)[0] == "ALTERNATIVE"  # 3º com 9%


def test_review_ready_ignores_only_scope_subreasons() -> None:
    scope = {
        "code": "OUT_OF_SCOPE",
        "subReasons": ["CHAMPION_OUT_OF_SCOPE", "MAIN_ITEM_ORDINAL_BEYOND_SECOND"],
    }
    assert is_review_ready([])
    assert is_review_ready([scope])
    assert not is_review_ready(
        [scope, {"code": "INVENTORY_UNCERTAIN", "subReasons": ["UNDO_UNCORRELATED"]}]
    )
    assert not is_review_ready([{"code": "OUT_OF_SCOPE", "subReasons": ["ROLE_UNKNOWN"]}])
    assert not is_review_ready([{"code": "TARGET_REVERTED", "subReasons": ["REVERTED_BY_UNDO"]}])


def test_skill_priority_is_the_order_skills_reach_rank_five() -> None:
    q, w, e, r = 1, 2, 3, 4
    order = [q, w, e, q, q, r, q, e, q, e, r, e, e, w, w, r, w, w]
    assert skill_priority([(slot, "NORMAL") for slot in order]) == "Q>E>W"
    assert skill_priority([(q, "NORMAL"), (w, "NORMAL"), (e, "NORMAL")]) is None
    evolve = [(q, "EVOLVE")] * 5 + [(w, "NORMAL")] * 5
    assert skill_priority(evolve) == "W>Q>E"


def test_skill_labels() -> None:
    reference = _reference(
        {}, {"86:TOP": {"n": 100, "priorities": [["Q>E>W", 70], ["E>Q>W", 20], ["Q>W>E", 10]]}}
    )
    assert reference.label_skills(86, "TOP", "Q>E>W")[0] == "MOST_COMMON"
    assert reference.label_skills(86, "TOP", "E>Q>W")[0] == "ALTERNATIVE"
    assert reference.label_skills(86, "TOP", "Q>W>E")[0] == "UNCOMMON"
    assert reference.label_skills(86, "TOP", None)[0] == "NO_REFERENCE"
    assert reference.label_skills(86, "MIDDLE", "Q>E>W")[0] == "NO_REFERENCE"
