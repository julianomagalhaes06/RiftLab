"""classify_catalog (CC-204): classificação pura de item principal. Catálogos
sintéticos mínimos que reproduzem os achados nos catálogos reais do Data
Dragon (destino excluído da curadoria, botas de nível 3 sem tag, depth não é
regra), a precedência das regras e os casos sem classificação confiável.
Sem rede, sem banco."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from riftlab.catalog.classification import (
    ITEM_CLASSIFICATION_VERSION,
    ItemClass,
    classify_catalog,
)
from riftlab.catalog.domain import Item
from riftlab.catalog.infrastructure.demo_source import DemoCatalogSource

_CATALOG_ID = uuid.UUID(int=1)
_V5 = "data_dragon_v5"


def _item(
    item_id: int,
    *,
    recipe: tuple[int, ...] = (),
    into: tuple[int, ...] = (),
    tags: tuple[str, ...] = (),
    purchasable: bool = True,
    maps: tuple[int, ...] = (11,),
    price: int = 1000,
    name: str = "Item Sintético",
    **flags: Any,
) -> Item:
    return Item(
        catalog_id=_CATALOG_ID,
        item_id=item_id,
        price=price,
        recipe=[{"itemId": component, "quantity": 1} for component in recipe],
        attributes={"name": name, "tags": list(tags), "stats": {}, "into": list(into)},
        flags={"purchasable": purchasable, "maps": list(maps), **flags},
    )


def _classify(*items: Item, excluded: frozenset[int] = frozenset(), version: str = _V5):
    return classify_catalog(
        {item.item_id: item for item in items},
        curation_rules_version=version,
        excluded_item_ids=excluded,
    )


def test_table_carries_classification_and_curation_versions() -> None:
    table = _classify(_item(1))
    assert table.classification_version == ITEM_CLASSIFICATION_VERSION == "item_classification_v1"
    assert table.curation_rules_version == _V5


def test_item_whose_destination_was_excluded_is_component_not_main() -> None:
    """Achado (2421 no Data Dragon real): tem `into`, mas o destino foi
    excluído da curadoria. No grafo curado ninguém o usa como componente e
    ele pareceria terminal — a classificação usa o `into` persistido."""
    base_a, base_b = _item(10, into=(20,)), _item(11, into=(20,))
    shattered = _item(20, recipe=(10, 11), into=(999,))  # 999 excluído, ausente do catálogo
    table = _classify(base_a, base_b, shattered, excluded=frozenset({999}))

    curated_dependents = [
        item for item in (base_a, base_b, shattered) if any(c["itemId"] == 20 for c in item.recipe)
    ]
    assert curated_dependents == []  # terminal no grafo curado
    assert table.classify(20).item_class is ItemClass.COMPONENT
    assert table.classify(20).rule_code == "DD5_HAS_INTO"
    assert table.classify(999).item_class is ItemClass.EXCLUDED


def test_level_three_boots_without_boots_tag_are_boots_by_transitive_recipe() -> None:
    """Achado (3171/3172 no Data Dragon 15.1.1): sem a tag Boots; só a
    receita as liga a botas — inclusive por mais de um nível."""
    boots_l1 = _item(1001, tags=("Boots",), into=(3006,))
    boots_l2 = _item(3006, recipe=(1001,), tags=("Boots",), into=(3171,))
    boots_l3 = _item(3171, recipe=(3006,))  # sem tag
    boots_l4 = _item(3172, recipe=(3171,))  # dois níveis abaixo da tag
    table = _classify(boots_l1, boots_l2, boots_l3, boots_l4)

    assert table.classify(1001).rule_code == "DD5_BOOTS_TAG"
    assert table.classify(3171).item_class is ItemClass.BOOTS
    assert table.classify(3171).rule_code == "DD5_BOOTS_RECIPE"
    assert table.classify(3172).item_class is ItemClass.BOOTS


def test_recipe_depth_is_not_a_rule() -> None:
    """Achado (3031/3089): depth 2 e lendários — profundidade nunca decide."""
    part_a, part_b = _item(30, into=(31,)), _item(31, into=(32,), recipe=(30,))
    depth_two = _item(40, recipe=(30, 30))
    depth_three = _item(32, recipe=(31, 30))
    table = _classify(part_a, part_b, depth_two, depth_three)

    assert table.classify(40).item_class is ItemClass.MAIN
    assert table.classify(32).item_class is ItemClass.MAIN
    assert table.classify(31).item_class is ItemClass.COMPONENT


@pytest.mark.parametrize(
    ("overrides", "expected", "rule_code"),
    [
        (
            {"purchasable": False, "tags": ("Trinket",)},
            ItemClass.NOT_PURCHASABLE,
            "DD5_NOT_PURCHASABLE",
        ),
        ({"maps": (12,), "tags": ("Consumable",)}, ItemClass.MAP_ILLEGAL, "DD5_MAP_ILLEGAL"),
        (
            {"requiredChampion": "Viktor", "tags": ("Trinket",)},
            ItemClass.CHAMPION_SPECIFIC,
            "DD5_CHAMPION_SPECIFIC",
        ),
        ({"tags": ("Trinket", "Consumable")}, ItemClass.TRINKET, "DD5_TRINKET_TAG"),
        ({"tags": ("Consumable", "Boots")}, ItemClass.CONSUMABLE, "DD5_CONSUMABLE_TAG"),
        ({"consumed": True, "tags": ("Boots",)}, ItemClass.CONSUMABLE, "DD5_CONSUMABLE_FLAG"),
        ({"tags": ("Boots",), "into": (2,)}, ItemClass.BOOTS, "DD5_BOOTS_TAG"),
        ({"into": (2,), "recipe": (3,)}, ItemClass.COMPONENT, "DD5_HAS_INTO"),
        ({}, ItemClass.BASIC_OR_STARTER, "DD5_NO_RECIPE"),
        ({"recipe": (3,)}, ItemClass.MAIN, "DD5_MAIN"),
    ],
)
def test_first_matching_rule_decides(
    overrides: dict[str, Any], expected: ItemClass, rule_code: str
) -> None:
    table = _classify(_item(1, **overrides), _item(2), _item(3, into=(1,)))
    classification = table.classify(1)
    assert classification.item_class is expected
    assert classification.rule_code == rule_code
    assert classification.reason  # motivo sempre presente


def test_name_description_and_price_are_never_a_rule() -> None:
    """Nome com cara de bota/trinket e preço extremo não mudam a classe."""
    lookalike = _item(1, recipe=(2,), name="Boots of Trinket Consumable", price=0)
    expensive = _item(3, recipe=(2,), name="Item Sintético", price=99999)
    table = _classify(lookalike, expensive, _item(2, into=(1, 3)))
    assert table.classify(1).item_class is ItemClass.MAIN
    assert table.classify(3).item_class is ItemClass.MAIN


@pytest.mark.parametrize("version", ["data_dragon_v4", "data_dragon_v3", "qualquer_coisa"])
def test_unsupported_curation_makes_every_item_unclassified(version: str) -> None:
    table = _classify(_item(1, recipe=(2,)), _item(2, into=(1,)), version=version)
    for item_id in (1, 2):
        classification = table.classify(item_id)
        assert classification.item_class is ItemClass.UNCLASSIFIED
        assert classification.rule_code == "CLASSIFICATION_UNSUPPORTED_CURATION"


@pytest.mark.parametrize(
    "broken",
    [
        Item(
            _CATALOG_ID,
            1,
            100,
            [],
            {"name": "x", "tags": [], "stats": {}},
            {"purchasable": True, "maps": [11]},
        ),
        Item(
            _CATALOG_ID,
            1,
            100,
            [],
            {"name": "x", "tags": [], "stats": {}, "into": ["2"]},
            {"purchasable": True, "maps": [11]},
        ),
        Item(
            _CATALOG_ID,
            1,
            100,
            [],
            {"name": "x", "tags": [], "stats": {}, "into": []},
            {"maps": [11]},
        ),
        Item(
            _CATALOG_ID,
            1,
            100,
            [],
            {"name": "x", "tags": [], "stats": {}, "into": []},
            {"purchasable": True, "maps": ["11"]},
        ),
        Item(
            _CATALOG_ID,
            1,
            100,
            [],
            {"name": "x", "tags": "Boots", "stats": {}, "into": []},
            {"purchasable": True, "maps": [11]},
        ),
    ],
    ids=["into-ausente", "into-nao-int", "purchasable-ausente", "maps-nao-int", "tags-nao-lista"],
)
def test_v5_item_with_missing_or_invalid_persisted_field_is_unclassified(broken: Item) -> None:
    classification = classify_catalog({1: broken}, curation_rules_version=_V5).classify(1)
    assert classification.item_class is ItemClass.UNCLASSIFIED
    assert classification.rule_code == "CLASSIFICATION_MISSING_FIELD"


def test_unknown_and_excluded_are_distinct_from_not_main() -> None:
    table = _classify(_item(1, recipe=(2,)), _item(2, into=(1,)), excluded=frozenset({500, 1}))
    assert table.classify(999).item_class is ItemClass.UNKNOWN
    assert table.classify(500).item_class is ItemClass.EXCLUDED
    # item presente E excluído (entrada inconsistente): excluído prevalece
    assert table.classify(1).item_class is ItemClass.EXCLUDED
    non_main = {
        ItemClass.NOT_PURCHASABLE,
        ItemClass.MAP_ILLEGAL,
        ItemClass.CHAMPION_SPECIFIC,
        ItemClass.TRINKET,
        ItemClass.CONSUMABLE,
        ItemClass.BOOTS,
        ItemClass.COMPONENT,
        ItemClass.BASIC_OR_STARTER,
    }
    assert table.classify(999).item_class not in non_main
    assert table.classify(500).item_class not in non_main


def test_recipe_cycle_terminates_without_boots_verdict() -> None:
    table = _classify(_item(1, recipe=(2,), into=(2,)), _item(2, recipe=(1,), into=(1,)))
    assert table.classify(1).item_class is ItemClass.COMPONENT  # terminou, sem loop infinito


def test_classification_is_pure_and_deterministic() -> None:
    items = {
        1: _item(1, recipe=(2,)),
        2: _item(2, into=(1,), tags=("Boots",)),
    }
    snapshot = {key: (item.recipe, item.attributes, item.flags) for key, item in items.items()}
    first = classify_catalog(items, curation_rules_version=_V5)
    second = classify_catalog(items, curation_rules_version=_V5)
    assert first == second
    assert {key: (i.recipe, i.attributes, i.flags) for key, i in items.items()} == snapshot


def test_demo_curation_uses_explicit_completed_flag() -> None:
    inconsistent = Item(_CATALOG_ID, 3, 10, [], {}, {"completed": True})
    missing = Item(_CATALOG_ID, 4, 10, [], {}, {})
    non_bool = Item(_CATALOG_ID, 5, 10, [{"itemId": 1, "quantity": 1}], {}, {"completed": "sim"})
    table = classify_catalog(
        {3: inconsistent, 4: missing, 5: non_bool}, curation_rules_version="curated_demo_v1"
    )
    assert table.classify(3).rule_code == "DEMO1_COMPLETED_WITHOUT_RECIPE"
    assert table.classify(4).rule_code == "CLASSIFICATION_MISSING_FIELD"
    assert table.classify(5).rule_code == "CLASSIFICATION_MISSING_FIELD"
    assert all(
        table.classify(item_id).item_class is ItemClass.UNCLASSIFIED for item_id in (3, 4, 5)
    )


async def test_demo_fixture_catalog_classification() -> None:
    result = await DemoCatalogSource().load_catalog("DEMO-1")
    items = {
        dto.item_id: Item(
            _CATALOG_ID, dto.item_id, dto.price, dto.recipe, dto.attributes, dto.flags
        )
        for dto in result.items
    }
    table = classify_catalog(items, curation_rules_version=result.curation_rules_version)

    assert table.classify(100010).item_class is ItemClass.MAIN
    assert table.classify(100010).rule_code == "DEMO1_COMPLETED"
    assert table.classify(100001).item_class is ItemClass.COMPONENT
    assert table.classify(100002).item_class is ItemClass.COMPONENT


def test_missing_recipe_component_is_unclassified_not_main() -> None:
    """Botas nível 2 sem o componente (tag Boots) no mapa recebido viravam MAIN
    em silêncio; com o catálogo completo é BOOTS."""
    boots = _item(1001, tags=("Boots",), into=(3006,))
    tier2 = _item(3006, recipe=(1001,))
    full = _classify(boots, tier2)
    partial = _classify(tier2)

    assert full.classify(3006).item_class is ItemClass.BOOTS
    assert partial.classify(3006).item_class is ItemClass.UNCLASSIFIED
    assert partial.classify(3006).rule_code == "CLASSIFICATION_INCOMPLETE_RECIPE_CLOSURE"


def test_missing_nested_component_is_unclassified() -> None:
    tier2 = _item(3006, recipe=(1001,), tags=())
    tier3 = _item(3172, recipe=(3006,))  # 1001 (fundo da cadeia) ausente
    table = _classify(tier2, tier3)
    assert table.classify(3172).item_class is ItemClass.UNCLASSIFIED
    assert table.classify(3006).item_class is ItemClass.UNCLASSIFIED


def test_positive_boots_evidence_wins_even_with_another_component_missing() -> None:
    boots = _item(1001, tags=("Boots",), into=(3006,))
    tier2 = _item(3006, recipe=(1001, 999))  # 999 ausente, mas 1001 prova botas
    table = _classify(boots, tier2)
    assert table.classify(3006).item_class is ItemClass.BOOTS
    assert table.classify(3006).rule_code == "DD5_BOOTS_RECIPE"


@pytest.mark.parametrize(
    "bad_entry", [{"quantity": 1}, {"itemId": "1001", "quantity": 1}, {"itemId": True}, "1001"]
)
def test_malformed_recipe_entry_is_unclassified_not_main(bad_entry: Any) -> None:
    item = _item(3006)
    item.recipe.append(bad_entry)
    table = _classify(item)
    assert table.classify(3006).item_class is ItemClass.UNCLASSIFIED
    assert table.classify(3006).rule_code == "CLASSIFICATION_INCOMPLETE_RECIPE_CLOSURE"


def test_classes_decided_before_recipe_rule_ignore_recipe_closure() -> None:
    """Regras 1-5 e a tag Boots não dependem da receita: componente ausente
    não os afeta."""
    consumable = _item(1, recipe=(999,), tags=("Consumable",))
    not_purchasable = _item(2, recipe=(999,), purchasable=False)
    tagged_boots = _item(3, recipe=(999,), tags=("Boots",))
    table = _classify(consumable, not_purchasable, tagged_boots)
    assert table.classify(1).item_class is ItemClass.CONSUMABLE
    assert table.classify(2).item_class is ItemClass.NOT_PURCHASABLE
    assert table.classify(3).item_class is ItemClass.BOOTS


def test_complete_closure_result_is_unchanged() -> None:
    """Com fechamento completo o resultado não muda."""
    base_a = _item(30, into=(31,))
    depth_two = _item(31, recipe=(30,))
    table = _classify(base_a, depth_two)
    assert table.classify(31).item_class is ItemClass.MAIN
    assert table.classify(30).item_class is ItemClass.COMPONENT
