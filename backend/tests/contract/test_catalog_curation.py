"""curate_data_dragon_items (CC-202, revisão complementar): regressão
persistida dos achados de curadoria permissiva — testa comportamento e
falha antes da persistência, não espelha a implementação. Payload sintético
mínimo, sem rede, sem dado de partida."""

from __future__ import annotations

import math
from typing import Any

import pytest

from riftlab.catalog.infrastructure.curation import curate_data_dragon_items
from riftlab.shared.errors import SourceSchemaError


def _base_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": "Item Sintético",
        "gold": {"base": 100, "purchasable": True, "total": 100, "sell": 70},
        "maps": {"11": True, "12": False},
        "tags": ["Damage"],
        "stats": {"FlatPhysicalDamageMod": 10},
    }
    item.update(overrides)
    return item


def _document(**overrides: Any) -> dict[str, Any]:
    return {"1042": _base_item(**overrides)}


def test_curate_accepts_valid_payload() -> None:
    items, excluded = curate_data_dragon_items(_document())

    assert len(items) == 1
    assert items[0].item_id == 1042
    assert items[0].price == 100
    assert excluded == ()


def test_curate_rejects_from_with_non_numeric_component() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        curate_data_dragon_items(_document(**{"from": ["bad"]}))
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


def test_curate_preserves_from_component_multiplicity() -> None:
    items, _ = curate_data_dragon_items(_document(**{"from": ["1001", "1001"]}))
    assert items[0].recipe == [{"itemId": 1001, "quantity": 2}]  # nunca colapsado a 1


def test_curate_rejects_maps_with_non_numeric_key() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        curate_data_dragon_items(_document(maps={"bad": True}))
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


def test_curate_rejects_maps_value_that_is_not_boolean() -> None:
    """Achado real da revisão: maps={'11': 'yes'} era aceito e virava lista
    vazia, confundindo valor inválido com mapa legitimamente ausente/false."""
    with pytest.raises(SourceSchemaError) as excinfo:
        curate_data_dragon_items(_document(maps={"11": "yes"}))
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


def test_curate_rejects_stats_value_that_is_not_numeric() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        curate_data_dragon_items(_document(stats={"FlatHPPoolMod": "bad"}))
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


def test_curate_rejects_stats_value_that_is_boolean() -> None:
    with pytest.raises(SourceSchemaError):
        curate_data_dragon_items(_document(stats={"FlatHPPoolMod": True}))


@pytest.mark.parametrize("bad_value", [math.inf, -math.inf, math.nan])
def test_curate_rejects_stats_value_that_is_not_finite(bad_value: float) -> None:
    with pytest.raises(SourceSchemaError):
        curate_data_dragon_items(_document(stats={"FlatHPPoolMod": bad_value}))


def test_curate_rejects_name_of_wrong_type() -> None:
    """Distinção deliberada (revisão): name de tipo errado (não string) é
    falha estrutural, nunca tratada como o mesmo caso de name="" vazio."""
    with pytest.raises(SourceSchemaError) as excinfo:
        curate_data_dragon_items(_document(name=123))
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


def test_curate_rejects_name_that_is_none() -> None:
    with pytest.raises(SourceSchemaError):
        curate_data_dragon_items(_document(name=None))


def test_curate_excludes_empty_string_name_with_reason_not_error() -> None:
    """name="" (string vazia, tipo certo) é o achado real de rede conhecido
    — excluído com motivo, nunca levanta e nunca derruba o catálogo."""
    items, excluded = curate_data_dragon_items(_document(name=""))

    assert items == ()
    assert len(excluded) == 1
    assert excluded[0].item_id == 1042
    assert excluded[0].reason_code == "MISSING_NAME_UNSUPPORTED"


def test_curate_excludes_special_recipe_item_with_reason() -> None:
    items, excluded = curate_data_dragon_items(_document(specialRecipe=9999))

    assert items == ()
    assert excluded[0].reason_code == "SPECIAL_RECIPE_UNSUPPORTED"


def test_curate_cascades_exclusion_to_item_built_from_excluded_component() -> None:
    document = {
        "1": _base_item(specialRecipe=9999),
        "2": _base_item(**{"from": ["1"]}),
    }
    items, excluded = curate_data_dragon_items(document)

    assert items == ()
    reasons = {e.item_id: e.reason_code for e in excluded}
    assert reasons[1] == "SPECIAL_RECIPE_UNSUPPORTED"
    assert reasons[2] == "COMPONENT_UNSUPPORTED"


def test_curate_rejects_duplicate_item_id_after_key_normalization() -> None:
    """Achado real da revisão: chaves brutas distintas ("007" e "7") podem
    colapsar no mesmo item_id inteiro — rejeitado explicitamente, nunca a
    segunda sobrescreve a primeira silenciosamente."""
    document = {"007": _base_item(), "7": _base_item()}
    with pytest.raises(SourceSchemaError) as excinfo:
        curate_data_dragon_items(document)
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"
    assert "duplicado" in str(excinfo.value)


def test_curate_rejects_missing_data_field() -> None:
    with pytest.raises(SourceSchemaError):
        curate_data_dragon_items(None)


def test_curate_rejects_item_that_is_not_an_object() -> None:
    with pytest.raises(SourceSchemaError):
        curate_data_dragon_items({"1042": "not-an-object"})


def test_curate_rejects_gold_purchasable_of_wrong_type() -> None:
    with pytest.raises(SourceSchemaError):
        curate_data_dragon_items(_document(gold={"total": 100, "purchasable": "yes"}))


def test_curate_rejects_negative_gold_total() -> None:
    with pytest.raises(SourceSchemaError):
        curate_data_dragon_items(_document(gold={"total": -1, "purchasable": True}))
