"""Validação pura de grafo de receita e tipos/limites de item (CC-202),
comum a qualquer CatalogSource (demo ou real) — sem I/O, sem Postgres."""

from __future__ import annotations

import pytest

from riftlab.catalog.domain import (
    CatalogItemSourceDTO,
    RecipeCycleError,
    RecipeReferenceError,
    validate_item_types_and_limits,
    validate_recipe_graph,
)
from riftlab.shared.errors import SourceSchemaError


def _item(item_id: int, price: int = 100, recipe: list[dict] | None = None) -> CatalogItemSourceDTO:
    return CatalogItemSourceDTO(item_id=item_id, price=price, recipe=recipe or [])


def test_validate_recipe_graph_accepts_valid_dag_with_shared_component() -> None:
    items = (
        _item(1),
        _item(2),
        _item(3, recipe=[{"itemId": 1, "quantity": 2}, {"itemId": 2, "quantity": 1}]),
    )
    validate_recipe_graph(items)  # não levanta


def test_validate_recipe_graph_rejects_missing_reference() -> None:
    items = (_item(1, recipe=[{"itemId": 999, "quantity": 1}]),)
    with pytest.raises(RecipeReferenceError) as excinfo:
        validate_recipe_graph(items)
    assert excinfo.value.code == "CATALOG_RECIPE_MISSING_REFERENCE"
    assert isinstance(excinfo.value, SourceSchemaError)


def test_validate_recipe_graph_rejects_direct_cycle() -> None:
    items = (_item(1, recipe=[{"itemId": 1, "quantity": 1}]),)
    with pytest.raises(RecipeCycleError) as excinfo:
        validate_recipe_graph(items)
    assert excinfo.value.code == "CATALOG_RECIPE_CYCLE"


def test_validate_recipe_graph_rejects_indirect_cycle() -> None:
    items = (
        _item(1, recipe=[{"itemId": 2, "quantity": 1}]),
        _item(2, recipe=[{"itemId": 3, "quantity": 1}]),
        _item(3, recipe=[{"itemId": 1, "quantity": 1}]),
    )
    with pytest.raises(RecipeCycleError):
        validate_recipe_graph(items)


def test_validate_recipe_graph_preserves_component_multiplicity_not_a_set() -> None:
    """Dois componentes iguais (ex.: duas Adagas reais no Data Dragon,
    ver curation.py) não podem colapsar para quantity=1 — a validação em si
    não recalcula quantidade, só confirma que a referência existe; este
    teste prova que uma quantity>1 explícita passa despreocupadamente
    (não é achatada por engano em algum ponto da validação)."""
    items = (_item(1), _item(2, recipe=[{"itemId": 1, "quantity": 2}]))
    validate_recipe_graph(items)  # não levanta
    assert items[1].recipe == [{"itemId": 1, "quantity": 2}]


def test_validate_item_types_and_limits_rejects_negative_price() -> None:
    items = (_item(1, price=-1),)
    with pytest.raises(SourceSchemaError) as excinfo:
        validate_item_types_and_limits(items)
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


def test_validate_item_types_and_limits_rejects_bool_as_item_id() -> None:
    items = (CatalogItemSourceDTO(item_id=True, price=100),)  # type: ignore[arg-type]
    with pytest.raises(SourceSchemaError):
        validate_item_types_and_limits(items)


def test_validate_item_types_and_limits_rejects_duplicate_item_id() -> None:
    items = (_item(1), _item(1))
    with pytest.raises(SourceSchemaError) as excinfo:
        validate_item_types_and_limits(items)
    assert "duplicado" in str(excinfo.value)


def test_validate_item_types_and_limits_rejects_zero_or_negative_quantity() -> None:
    items = (_item(1), _item(2, recipe=[{"itemId": 1, "quantity": 0}]))
    with pytest.raises(SourceSchemaError):
        validate_item_types_and_limits(items)


def test_validate_item_types_and_limits_rejects_component_quantity_as_bool() -> None:
    items = (_item(1), _item(2, recipe=[{"itemId": 1, "quantity": True}]))
    with pytest.raises(SourceSchemaError):
        validate_item_types_and_limits(items)


def test_validate_item_types_and_limits_accepts_valid_catalog() -> None:
    items = (_item(1), _item(2, recipe=[{"itemId": 1, "quantity": 3}]))
    validate_item_types_and_limits(items)  # não levanta
