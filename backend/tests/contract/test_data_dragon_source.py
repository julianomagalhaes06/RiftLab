"""DataDragonCatalogSource (CC-202) via httpx.MockTransport — nenhuma chamada
de rede real, nenhum dado de partida. Payload sintético modelado no schema
real inspecionado nesta sessão (Data Dragon 16.17.1, CDN público)."""

from __future__ import annotations

import json

import httpx
import pytest

from riftlab.catalog.infrastructure.catalog_mapping import parse_catalog_mapping_document
from riftlab.catalog.infrastructure.data_dragon_source import DataDragonCatalogSource
from riftlab.catalog.infrastructure.data_dragon_transport import DataDragonTransport
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import SourceNotFoundError, SourceSchemaError

_MAPPING = parse_catalog_mapping_document(
    b'schemaVersion: v1\nmappingVersion: v1\nmappings: {"16.17": {"dataDragonVersion": "16.17.1"}}'
)


def _item_document(**item_overrides_by_id: dict) -> bytes:
    base_items = {
        "1042": {
            "name": "Dagger",
            "gold": {"base": 250, "purchasable": True, "total": 250, "sell": 175},
            "maps": {"11": True, "12": True},
            "tags": ["AttackSpeed"],
            "stats": {"PercentAttackSpeedMod": 0.1},
        },
        "3035": {
            "name": "Last Whisper",
            "from": ["1042", "1042"],  # multiplicidade real: duas Adagas
            "gold": {"base": 100, "purchasable": True, "total": 600, "sell": 420},
            "maps": {"11": True, "12": True},
            "tags": ["ArmorPenetration"],
            "stats": {},
        },
        "223042": {
            "name": "Muramana",
            "specialRecipe": 223004,
            "gold": {"base": 2500, "purchasable": False, "total": 2500, "sell": 1750},
            "maps": {"11": True, "12": True},
            "tags": ["Damage"],
            "stats": {},
        },
        "3901": {
            "name": "Fire at Will",
            "requiredChampion": "Gangplank",
            "gold": {"base": 0, "purchasable": False, "total": 0, "sell": 0},
            "maps": {"11": True, "12": True},
            "tags": [],
            "stats": {},
        },
        # Achado real (rede real, fora da CI): item 2008 do Data Dragon
        # 16.17.1 tem name="" de verdade — artefato interno/legado, não um
        # schema quebrado. Reproduzido aqui como regressão sintética.
        "2008": {
            "name": "",
            "inStore": False,
            "gold": {"base": 60, "purchasable": False, "total": 60, "sell": 42},
            "maps": {"11": False, "12": False},
            "tags": [],
            "stats": {},
        },
        "2003": {
            "name": "Health Potion",
            "consumed": True,
            "stacks": 5,
            "gold": {"base": 50, "purchasable": True, "total": 50, "sell": 20},
            "maps": {"11": True, "12": True, "22": False},
            "tags": ["Consumable"],
            "stats": {},
        },
    }
    for item_id, overrides in item_overrides_by_id.items():
        base_items.setdefault(item_id, {}).update(overrides)
    return json.dumps({"type": "item", "version": "16.17.1", "data": base_items}).encode()


def _handler(status: int = 200, body: bytes | None = None):
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(status, content=body if body is not None else _item_document())

    handle.calls = calls  # type: ignore[attr-defined]
    return handle


def _source(handler) -> DataDragonCatalogSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = DataDragonTransport(client=client)
    return DataDragonCatalogSource(transport, _MAPPING)


async def test_load_catalog_curates_items_with_preserved_multiplicity() -> None:
    source = _source(_handler())

    result = await source.load_catalog("16.17")

    assert result.origin is SourceOrigin.COMPLETED_MATCH
    assert result.data_dragon_version == "16.17.1"
    by_id = {item.item_id: item for item in result.items if item.item_id in (1042, 3035)}
    last_whisper = by_id[3035]
    assert last_whisper.recipe == [{"itemId": 1042, "quantity": 2}]  # nunca colapsado a 1
    assert last_whisper.price == 600


async def test_load_catalog_excludes_special_recipe_items_with_reason() -> None:
    source = _source(_handler())

    result = await source.load_catalog("16.17")

    assert 223042 not in {item.item_id for item in result.items}
    excluded_ids = {excluded.item_id: excluded for excluded in result.excluded_items}
    assert 223042 in excluded_ids
    assert excluded_ids[223042].reason_code == "SPECIAL_RECIPE_UNSUPPORTED"


async def test_load_catalog_cascades_exclusion_to_items_built_from_excluded_component() -> None:
    """Achado real (rede real fora da CI, Data Dragon 16.17.1): os 5
    upgrades míticos de "Bounty of Worlds" (item 3867, excluído por
    specialRecipe) declaram from=["3867"] — uma receita numérica normal que
    referencia um item que NÃO é reconciliável numericamente. Nunca tratado
    como referência inexistente (não derruba o catálogo inteiro) nem como
    componente normal (o item base não tem preço/receita real) — o
    dependente é excluído em cascata."""
    handler = _handler(
        body=_item_document(
            **{
                "3867": {
                    "name": "Bounty of Worlds",
                    "specialRecipe": 3866,
                    "gold": {"base": 400, "purchasable": False, "total": 400, "sell": 160},
                    "maps": {"11": True},
                    "tags": [],
                    "stats": {},
                },
                "3869": {
                    "name": "Celestial Opposition",
                    "from": ["3867"],
                    "gold": {"base": 0, "purchasable": True, "total": 400, "sell": 280},
                    "maps": {"11": True},
                    "tags": [],
                    "stats": {},
                },
            }
        )
    )
    source = _source(handler)

    result = await source.load_catalog("16.17")

    item_ids = {item.item_id for item in result.items}
    assert 3867 not in item_ids
    assert 3869 not in item_ids
    excluded_ids = {excluded.item_id: excluded for excluded in result.excluded_items}
    assert excluded_ids[3867].reason_code == "SPECIAL_RECIPE_UNSUPPORTED"
    assert excluded_ids[3869].reason_code == "COMPONENT_UNSUPPORTED"


async def test_load_catalog_excludes_items_without_name_with_reason() -> None:
    """Achado real (rede real contra 16.17.1, fora da CI): item 2008 tem
    name="" de verdade. Nunca inventa um nome nem derruba o catálogo
    inteiro — exclui com motivo, mesmo caminho de specialRecipe."""
    source = _source(_handler())

    result = await source.load_catalog("16.17")

    assert 2008 not in {item.item_id for item in result.items}
    excluded_ids = {excluded.item_id: excluded for excluded in result.excluded_items}
    assert 2008 in excluded_ids
    assert excluded_ids[2008].reason_code == "MISSING_NAME_UNSUPPORTED"


async def test_load_catalog_curates_required_champion_and_purchasable_flags() -> None:
    source = _source(_handler())

    result = await source.load_catalog("16.17")

    by_id = {item.item_id: item for item in result.items}
    assert by_id[3901].flags["requiredChampion"] == "Gangplank"
    assert by_id[3901].flags["purchasable"] is False
    assert by_id[2003].flags["consumed"] is True
    assert by_id[2003].flags["stacks"] == 5


async def test_load_catalog_rejects_unmapped_patch_without_network_call() -> None:
    handler = _handler()
    source = _source(handler)

    with pytest.raises(SourceNotFoundError) as excinfo:
        await source.load_catalog("13.1")  # fora do mapping de teste

    assert excinfo.value.code == "MISSING_CATALOG"
    assert handler.calls == []  # nunca tenta rede para um patch sem mapping


async def test_load_catalog_propagates_permanent_not_found_for_missing_version() -> None:
    source = _source(_handler(status=404))

    with pytest.raises(SourceNotFoundError) as excinfo:
        await source.load_catalog("16.17")
    assert excinfo.value.code == "DATA_DRAGON_VERSION_NOT_FOUND"


async def test_load_catalog_rejects_body_with_divergent_version() -> None:
    """Achado real da revisão complementar: a URL correta não prova que o
    corpo é correto (proxy/cache/versão trocada) — o body pode declarar uma
    version diferente da pedida mesmo respondendo 200 na URL certa."""
    body = json.dumps({"type": "item", "version": "99.99.1", "data": {}}).encode()
    source = _source(_handler(body=body))

    with pytest.raises(SourceSchemaError) as excinfo:
        await source.load_catalog("16.17")
    assert excinfo.value.code == "DATA_DRAGON_VERSION_MISMATCH"


async def test_load_catalog_rejects_body_with_missing_version_field() -> None:
    body = json.dumps({"type": "item", "data": {}}).encode()
    source = _source(_handler(body=body))

    with pytest.raises(SourceSchemaError) as excinfo:
        await source.load_catalog("16.17")
    assert excinfo.value.code == "DATA_DRAGON_VERSION_MISMATCH"


async def test_load_catalog_rejects_body_with_wrong_type_field() -> None:
    body = json.dumps({"type": "champion", "version": "16.17.1", "data": {}}).encode()
    source = _source(_handler(body=body))

    with pytest.raises(SourceSchemaError) as excinfo:
        await source.load_catalog("16.17")
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


async def test_load_catalog_accepts_body_with_matching_type_and_version() -> None:
    """Caminho válido: type/version do corpo batendo com o pedido nunca é
    rejeitado pela checagem nova."""
    result = await _source(_handler()).load_catalog("16.17")
    assert result.data_dragon_version == "16.17.1"


async def test_load_catalog_rejects_invalid_json_body() -> None:
    source = _source(_handler(body=b"{not valid json"))

    with pytest.raises(SourceSchemaError) as excinfo:
        await source.load_catalog("16.17")
    assert excinfo.value.code == "CATALOG_SOURCE_INVALID_JSON"


async def test_load_catalog_rejects_item_without_gold_field() -> None:
    body = json.dumps(
        {"type": "item", "version": "16.17.1", "data": {"1": {"name": "Broken"}}}
    ).encode()
    source = _source(_handler(body=body))

    with pytest.raises(SourceSchemaError) as excinfo:
        await source.load_catalog("16.17")
    assert excinfo.value.code == "CATALOG_SOURCE_MISSING_FIELD"


async def test_load_catalog_rejects_recipe_referencing_missing_component() -> None:
    body = json.dumps(
        {
            "type": "item",
            "version": "16.17.1",
            "data": {
                "1": {
                    "name": "Órfão",
                    "from": ["9999"],
                    "gold": {"base": 10, "purchasable": True, "total": 10, "sell": 5},
                    "maps": {"11": True},
                    "tags": [],
                    "stats": {},
                }
            },
        }
    ).encode()
    source = _source(_handler(body=body))

    with pytest.raises(SourceSchemaError) as excinfo:
        await source.load_catalog("16.17")
    assert excinfo.value.code == "CATALOG_RECIPE_MISSING_REFERENCE"
