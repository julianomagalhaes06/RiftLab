"""CC-1502: só tolera incerteza de itens que não mudam candidatos principais."""

from __future__ import annotations

import uuid

from riftlab.catalog.classification import classify_catalog
from riftlab.catalog.domain import Item
from riftlab.decisions.features import GoldView, LevelView, PreDecisionView, PreReason
from riftlab.decisions.tolerance import tolerate

CATALOG = uuid.UUID(int=9)


def item(item_id: int, recipe=(), tags=(), into=(), **flags) -> Item:
    return Item(
        CATALOG,
        item_id,
        1000,
        [{"itemId": c, "quantity": 1} for c in recipe],
        {"name": str(item_id), "tags": list(tags), "stats": {}, "into": list(into)},
        {"purchasable": True, "maps": [11], **flags},
    )


ITEMS = {
    1001: item(1001, tags=("Boots",), into=(3020,)),
    3020: item(3020, recipe=(1001,), tags=("Boots",)),
    2055: item(2055, tags=("Consumable",)),
    1036: item(1036, into=(3071,)),
    3071: item(3071, recipe=(1036,)),
}
TABLE = classify_catalog(
    ITEMS, curation_rules_version="data_dragon_v5", excluded_item_ids=frozenset()
)


def view(items, reasons, uncertain=True) -> PreDecisionView:
    return PreDecisionView(
        uuid.UUID(int=1),
        "M",
        1,
        10,
        1000,
        tuple(items),
        3340,
        GoldView(None, "UNKNOWN", None),
        LevelView(None, None, None),
        uncertain,
        tuple(PreReason(code, item_id) for code, item_id in reasons),
    )


def test_clean_view_passes_untouched():
    clean = view([(3071, 1)], [], uncertain=False)
    result = tolerate(clean, TABLE)
    assert result is not None and not result.tolerated and result.view is clean


def test_boots_and_wards_are_tolerated_and_removed():
    result = tolerate(
        view(
            [(3071, 1), (3020, 1), (2055, 1)],
            [("DESTROY_WITHOUT_OWNERSHIP", 3020), ("UNRECONCILED_ITEM_DESTROYED", 2055)],
        ),
        TABLE,
    )
    assert result is not None and result.tolerated
    assert result.view.inventory_items == ((3071, 1),)
    assert not result.view.pre_decision_uncertain and result.removed_item_ids == (2055, 3020)


def test_doubt_about_main_item_component_or_unknown_reason_is_not_tolerated():
    assert tolerate(view([(3071, 1)], [("DESTROY_WITHOUT_OWNERSHIP", 3071)]), TABLE) is None
    assert tolerate(view([(1036, 1)], [("SALE_WITHOUT_OWNERSHIP", 1036)]), TABLE) is None
    assert tolerate(view([(3071, 1)], [("UNDO_UNCORRELATED", None)]), TABLE) is None
    assert tolerate(view([(3071, 1)], []), TABLE) is None  # incerto sem motivo
