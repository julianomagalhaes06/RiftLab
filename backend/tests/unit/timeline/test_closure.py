from dataclasses import replace
from uuid import uuid4

import pytest

from riftlab.catalog.classification import classify_catalog
from riftlab.catalog.diff import catalog_diff
from riftlab.catalog.domain import Item
from riftlab.ingestion.final_items import FinalItems, parse_final_items
from riftlab.shared.errors import SourceSchemaError
from riftlab.timeline.application import InventorySnapshot
from riftlab.timeline.closure import closure_detail, verdict


def test_quest_slot_and_grants_are_not_normal_inventory():
    # Mesma política de comparação do congelamento das regras de concessão/botas.
    official = FinalItems((2010, 2422, 2150, 2151, 2152, 3006), 3340, 3009)
    predicted = InventorySnapshot(((3006, 1),), 3340)
    assert not closure_detail(predicted, official, {}).failed


def test_absent_trinket_is_a_real_failure():
    result = closure_detail(InventorySnapshot(trinket_item_id=3340), FinalItems(()), {})
    assert result.failed and not result.trinket_ok


def test_consumable_presence_but_component_multiplicity():
    item = Item(uuid4(), 2003, 50, flags={"consumed": True})
    result = closure_detail(
        InventorySnapshot(((2003, 3), (1036, 2))), FinalItems((2003, 1036)), {2003: item}
    )
    assert result.extra == {1036: 1} and not result.missing


@pytest.mark.parametrize(
    ("n", "k", "m", "expected"),
    [
        (0, 0, 0, "NO_SAMPLE"),
        (19, 0, 3, "NO_SAMPLE"),
        (100, 0, 2, "NO_SAMPLE"),
        (22, 0, 15, "REJECT"),
        (35, 0, 3, "ACCEPT"),
        (22, 6, 15, "REJECT"),
    ],
)
def test_wilson_gate(n, k, m, expected):
    outcome, upper = verdict(n, k, m)
    assert outcome == expected
    assert upper is not None and 0 <= upper <= 1


@pytest.mark.parametrize("value", [True, -1, 1.5, "²", "٣", "1.2", None, 2**31, "9" * 100])
def test_invalid_final_item_does_not_become_id(value):
    raw = {f"item{i}": 0 for i in range(7)}
    raw["item0"] = value
    with pytest.raises(SourceSchemaError):
        parse_final_items(raw)


def test_final_slots_preserve_empty_vs_absent_and_duplicates():
    assert parse_final_items({}) is None
    raw = {f"item{i}": 0 for i in range(7)}
    assert parse_final_items(raw) == FinalItems(())
    raw.update(item0="1036", item1=1036, item6="3340", roleBoundItem=3006)
    assert parse_final_items(raw) == FinalItems((1036, 1036), 3340, 3006)
    with pytest.raises(SourceSchemaError):
        parse_final_items({"item0": 1036})


def test_catalog_price_alone_is_not_legality_change():
    cid = uuid4()
    a = Item(
        cid,
        1001,
        300,
        flags={"purchasable": True, "maps": [11]},
        attributes={"tags": ["Boots"], "into": []},
    )
    ca = {1001: a}
    cb = {1001: replace(a, price=400)}

    def classify(c):
        return classify_catalog(c, curation_rules_version="data_dragon_v5")

    assert catalog_diff(ca, cb, classify(ca), classify(cb)) == {}
    cb[1001] = replace(a, flags={"purchasable": False, "maps": [11]})
    assert set(catalog_diff(ca, cb, classify(ca), classify(cb))[1001]) == {
        "purchasable",
        "CLASSIFICATION",
    }

    class_b = {1001: replace(a, attributes={"tags": ["Starter"], "into": []})}
    assert classify(ca).classify(1001).item_class != classify(class_b).classify(1001).item_class
    assert "CLASSIFICATION" in catalog_diff(ca, class_b, classify(ca), classify(class_b))[1001]


def test_catalog_diff_ignores_order_of_map_and_recipe_members():
    cid = uuid4()
    a = Item(
        cid,
        1001,
        300,
        recipe=[{"itemId": 2001}, {"itemId": 2002}],
        flags={"purchasable": True, "maps": [11, 12]},
    )
    b = replace(
        a,
        recipe=[{"itemId": 2002}, {"itemId": 2001}],
        flags={"purchasable": True, "maps": [12, 11]},
    )

    def classify(c):
        return classify_catalog(c, curation_rules_version="data_dragon_v5")

    assert catalog_diff({1001: a}, {1001: b}, classify({1001: a}), classify({1001: b})) == {}
