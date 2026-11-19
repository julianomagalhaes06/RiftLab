"""CC-1504: G-FINAL restrita a itens principais e componentes, com a linhagem."""

from __future__ import annotations

from riftlab.catalog.classification import ItemClass
from riftlab.ingestion.final_items import FinalItems
from riftlab.timeline.application import InventorySnapshot
from riftlab.timeline.closure import closure_detail, main_closure_failed

CLASSES = {
    3004: ItemClass.MAIN,  # Manamune
    3042: ItemClass.EXCLUDED,  # Muramana (transformação)
    3071: ItemClass.MAIN,
    1036: ItemClass.COMPONENT,
    3865: ItemClass.BASIC_OR_STARTER,  # Atlas Mundial
    3870: ItemClass.EXCLUDED,  # etapa final do item de suporte
    3020: ItemClass.BOOTS,
    2055: ItemClass.CONSUMABLE,
}


def classify(item_id: int) -> ItemClass:
    return CLASSES.get(item_id, ItemClass.UNKNOWN)


def snapshot(*items: tuple[int, int]) -> InventorySnapshot:
    return InventorySnapshot(items=tuple(sorted(items)), trinket_item_id=3340)


def test_transforms_support_chain_boots_and_wards_do_not_fail_the_main_closure():
    predicted = snapshot((3004, 1), (3071, 1), (3865, 1), (3020, 1), (2055, 2))
    official = FinalItems((3042, 3071, 3870, 3006), 3340)
    assert closure_detail(predicted, official, {}).failed  # o G-FINAL completo reprova
    assert not main_closure_failed(predicted, official, classify)


def test_missing_main_item_or_component_fails():
    assert main_closure_failed(snapshot((3071, 1)), FinalItems((3071, 3004), 3340), classify)
    assert main_closure_failed(snapshot((3071, 1), (1036, 1)), FinalItems((3071,), 3340), classify)
