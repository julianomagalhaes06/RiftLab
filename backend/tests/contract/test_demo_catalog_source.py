"""Contrato do Protocol CatalogSource (CC-104, docs/domain-model.md). Mesma
disciplina do MatchSource: duas implementações locais, carga repetida, fixture
inválida com diagnóstico identificável, sem rede."""

from __future__ import annotations

import hashlib
import socket
from importlib import resources

import pytest

from riftlab.catalog.domain import CatalogSource, CatalogSourceResult
from riftlab.catalog.infrastructure.demo_source import (
    DemoCatalogSource,
    parse_catalog_source_document,
)
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import SourceNotFoundError, SourceSchemaError


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("chamada de rede bloqueada no teste offline de CatalogSource")

    monkeypatch.setattr(socket.socket, "connect", _blocked)


class FakeCatalogSource:
    """Implementação mínima do Protocol, sem I/O algum."""

    def __init__(self, results: dict[str, CatalogSourceResult]) -> None:
        self._results = results

    async def load_catalog(self, game_patch: str) -> CatalogSourceResult:
        try:
            return self._results[game_patch]
        except KeyError as exc:
            raise SourceNotFoundError(code="CATALOG_SOURCE_NOT_FOUND", detail=game_patch) from exc


async def _consume_catalog_source(source: CatalogSource, game_patch: str) -> CatalogSourceResult:
    """Consumidor tipado só no Protocol (verificado por `uv run mypy src`); não
    sabe se recebeu a fonte demo ou o fake mínimo."""
    return await source.load_catalog(game_patch)


async def test_demo_and_fake_implementations_are_interchangeable_via_the_protocol() -> None:
    demo_result = await _consume_catalog_source(DemoCatalogSource(), "DEMO-1")

    fake_result = await _consume_catalog_source(
        FakeCatalogSource({"FAKE-1": demo_result}), "FAKE-1"
    )

    assert fake_result == demo_result


async def test_demo_catalog_source_loads_items_with_reconciled_component_multiplicity() -> None:
    result = await DemoCatalogSource().load_catalog("DEMO-1")

    assert result.origin is SourceOrigin.CURATED_DEMO
    assert result.game_patch == "DEMO-1"
    by_id = {item.item_id: item for item in result.items}
    assert set(by_id) == {100001, 100002, 100010}

    completed = by_id[100010]
    recipe = {(component["itemId"], component["quantity"]) for component in completed.recipe}
    assert recipe == {(100001, 2), (100002, 1)}
    for component in completed.recipe:
        assert component["itemId"] in by_id  # nenhuma referência órfã no catálogo


async def test_demo_catalog_source_rejects_unknown_game_patch() -> None:
    with pytest.raises(SourceNotFoundError):
        await DemoCatalogSource().load_catalog("DEMO-999-DOES-NOT-EXIST")


async def test_demo_catalog_source_invalid_fixture_fails_identifiably() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        await DemoCatalogSource().load_catalog("DEMO-2-INVALID")

    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


async def test_demo_catalog_source_repeated_calls_are_independent_and_deterministic() -> None:
    source = DemoCatalogSource()

    first = await source.load_catalog("DEMO-1")
    second = await source.load_catalog("DEMO-1")

    assert first == second
    assert first.items is not second.items


async def test_demo_catalog_source_result_is_immutable() -> None:
    result = await DemoCatalogSource().load_catalog("DEMO-1")

    with pytest.raises(AttributeError):
        result.game_patch = "DEMO-CHANGED"  # type: ignore[misc]


async def test_demo_catalog_source_hash_reflects_raw_bytes_read() -> None:
    result = await DemoCatalogSource().load_catalog("DEMO-1")

    raw = (
        resources.files("riftlab.catalog.infrastructure.fixtures")
        .joinpath("catalogs", "demo_1.json")
        .read_bytes()
    )
    assert result.raw_hash == hashlib.sha256(raw).hexdigest()


def test_parse_catalog_source_document_rejects_invalid_json() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        parse_catalog_source_document(b"{broken")

    assert excinfo.value.code == "CATALOG_SOURCE_INVALID_JSON"


def test_parse_catalog_source_document_rejects_unknown_schema_version() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        parse_catalog_source_document(b'{"schemaVersion":"v0"}')

    assert excinfo.value.code == "SCHEMA_UNSUPPORTED"
