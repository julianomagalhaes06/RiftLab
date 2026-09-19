"""curate_data_dragon_items (CC-202, revisão complementar): regressão
persistida dos achados de curadoria permissiva — testa comportamento e
falha antes da persistência, não espelha a implementação. Payload sintético
mínimo, sem rede, sem dado de partida."""

from __future__ import annotations

import math
from typing import Any

import pytest

from riftlab.catalog.infrastructure.curation import (
    CURATION_RULES_VERSION,
    curate_data_dragon_items,
)
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


def test_curation_version_is_v5() -> None:
    assert CURATION_RULES_VERSION == "data_dragon_v5"


def test_curate_persists_into_as_sorted_int_list() -> None:
    items, _ = curate_data_dragon_items(_document(into=["3078", "3004", "3003"]))
    assert items[0].attributes["into"] == [3003, 3004, 3078]


def test_curate_missing_into_becomes_empty_list_only() -> None:
    items, _ = curate_data_dragon_items(_document())
    assert items[0].attributes["into"] == []  # ausente -> lista vazia, sem outro default


def test_curate_into_may_reference_an_excluded_or_absent_item() -> None:
    """`into` apontando para item excluído da curadoria (specialRecipe) ou
    inexistente no payload é esperado: nunca erro, nunca removido."""
    payload = {
        "1042": _base_item(into=["9001", "9999"]),
        "9001": _base_item(specialRecipe=123),
    }
    items, excluded = curate_data_dragon_items(payload)
    assert [item.item_id for item in items] == [1042]
    assert items[0].attributes["into"] == [9001, 9999]
    assert [entry.item_id for entry in excluded] == [9001]


@pytest.mark.parametrize("bad_into", ["3078", {"3078": True}, None, 3078])
def test_curate_rejects_into_that_is_not_a_list(bad_into: Any) -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        curate_data_dragon_items(_document(into=bad_into))
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


@pytest.mark.parametrize("bad_destination", ["abc", 3078, "-1", "", True, None])
def test_curate_rejects_into_with_non_numeric_destination(bad_destination: Any) -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        curate_data_dragon_items(_document(into=["3078", bad_destination]))
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


def test_curate_into_does_not_change_price_recipe_flags_or_exclusions() -> None:
    """`into` só acrescenta um atributo: o resto do resultado é idêntico ao
    do mesmo payload sem `into` (o resultado do reducer não pode mudar)."""
    base = {"1042": _base_item(**{"from": ["1001", "1001"]})}
    with_into = {"1042": _base_item(**{"from": ["1001", "1001"], "into": ["3078"]})}
    (plain,), plain_excluded = curate_data_dragon_items(base)
    (enriched,), enriched_excluded = curate_data_dragon_items(with_into)

    assert (enriched.price, enriched.recipe, enriched.flags) == (
        plain.price,
        plain.recipe,
        plain.flags,
    )
    assert {k: v for k, v in enriched.attributes.items() if k != "into"} == {
        k: v for k, v in plain.attributes.items() if k != "into"
    }
    assert plain_excluded == enriched_excluded == ()


# Dígitos não ASCII: str.isdigit() os aceita, int() rejeita ("²") ou converte
# em silêncio ("٣" -> 3). Todos os quatro pontos numéricos da curadoria (chave
# do item, chave de maps, from e into) rejeitam com SourceSchemaError.
_NON_ASCII_DIGITS = ["²", "٣", "３", "①"]


@pytest.mark.parametrize("bad", _NON_ASCII_DIGITS)
def test_curate_rejects_non_ascii_digits_in_into(bad: str) -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        curate_data_dragon_items(_document(into=[bad]))
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


@pytest.mark.parametrize("bad", _NON_ASCII_DIGITS)
def test_curate_rejects_non_ascii_digits_in_from(bad: str) -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        curate_data_dragon_items(_document(**{"from": [bad]}))
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


@pytest.mark.parametrize("bad", _NON_ASCII_DIGITS)
def test_curate_rejects_non_ascii_digits_in_maps_key(bad: str) -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        curate_data_dragon_items(_document(maps={bad: True}))
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


@pytest.mark.parametrize("bad", _NON_ASCII_DIGITS)
def test_curate_rejects_non_ascii_digits_in_item_key(bad: str) -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        curate_data_dragon_items({bad: _base_item()})
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


def test_curate_valid_ascii_digits_unchanged_by_ascii_only_check() -> None:
    """A checagem só ASCII não altera o resultado de entrada válida (por isso
    a versão da curadoria continua data_dragon_v5)."""
    items, _ = curate_data_dragon_items(
        {"1042": _base_item(**{"from": ["1001", "1001"], "into": ["3078", "3004"]})}
    )
    assert items[0].item_id == 1042
    assert items[0].recipe == [{"itemId": 1001, "quantity": 2}]
    assert items[0].attributes["into"] == [3004, 3078]
    assert items[0].flags["maps"] == [11]
