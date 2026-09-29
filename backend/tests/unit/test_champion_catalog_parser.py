"""Parser do champion.json (CC-208): recortes REAIS do Data Dragon 16.16.1 e 16.17.1
(pt_BR; cinco campeões com os campos originais) em tests/support/data_dragon/."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from riftlab.champions.domain import parse_champion_catalog
from riftlab.shared.errors import SourceSchemaError

_FIXTURES = Path(__file__).resolve().parents[1] / "support" / "data_dragon"


def _body(version: str) -> bytes:
    return (_FIXTURES / f"champion_{version}_pt_BR_subset.json").read_bytes()


def _doc(version: str) -> dict:
    return json.loads(_body(version))


@pytest.mark.parametrize("version", ["16.16.1", "16.17.1"])
def test_recorte_ids_resolve_by_numeric_key_in_both_pinned_patches(version: str) -> None:
    catalog = parse_champion_catalog(_body(version), expected_version=version, locale="pt_BR")
    by_key = {e.champion_key: e for e in catalog.entries}
    assert catalog.rejected == 0
    assert (by_key[145].name, by_key[145].image_file) == ("Kai'Sa", "Kaisa.png")
    assert (by_key[51].name, by_key[51].image_file) == ("Caitlyn", "Caitlyn.png")
    assert (by_key[81].name, by_key[81].image_file) == ("Ezreal", "Ezreal.png")


def test_numeric_key_not_text_id_names_the_file() -> None:
    """key 62 → id "MonkeyKing" → arquivo MonkeyKing.png e nome "Wukong": resolver
    pelo id textual ou pelo nome daria um arquivo inexistente."""
    catalog = parse_champion_catalog(_body("16.17.1"), expected_version="16.17.1", locale="pt_BR")
    wukong = next(e for e in catalog.entries if e.champion_key == 62)
    assert (wukong.champion_text_id, wukong.name, wukong.image_file) == (
        "MonkeyKing",
        "Wukong",
        "MonkeyKing.png",
    )
    assert all(str(e.champion_key) not in e.image_file for e in catalog.entries)


def test_version_divergent_from_the_pinned_one_is_rejected_whole() -> None:
    with pytest.raises(SourceSchemaError) as exc:
        parse_champion_catalog(_body("16.16.1"), expected_version="16.17.1", locale="pt_BR")
    assert exc.value.code == "CHAMPION_CATALOG_VERSION_MISMATCH"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda d: d.update(type="item"), "CHAMPION_CATALOG_WRONG_TYPE"),
        (lambda d: d.update(data={}), "CHAMPION_CATALOG_MISSING_DATA"),
    ],
)
def test_wrong_type_or_empty_data_is_rejected(mutate, code: str) -> None:
    doc = _doc("16.17.1")
    mutate(doc)
    with pytest.raises(SourceSchemaError) as exc:
        parse_champion_catalog(json.dumps(doc).encode(), expected_version="16.17.1", locale="pt_BR")
    assert exc.value.code == code


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(SourceSchemaError):
        parse_champion_catalog(b"{no", expected_version="16.17.1", locale="pt_BR")


@pytest.mark.parametrize(
    "bad_full",
    [
        "../Kaisa.png",
        "img/Kaisa.png",
        "https://evil.example/Kaisa.png",
        "Kaisa.jpg",
        "Kai sa.png",
        "",
    ],
)
def test_image_file_with_path_scheme_or_wrong_extension_is_never_published(bad_full: str) -> None:
    doc = _doc("16.17.1")
    doc["data"]["Kaisa"]["image"]["full"] = bad_full
    catalog = parse_champion_catalog(
        json.dumps(doc).encode(), expected_version="16.17.1", locale="pt_BR"
    )
    assert 145 not in {e.champion_key for e in catalog.entries}
    assert catalog.rejected == 1
    assert {e.champion_key for e in catalog.entries} == {51, 62, 81, 89}


@pytest.mark.parametrize(
    "field,value", [("key", "Kaisa"), ("key", "0145"), ("name", "  "), ("id", "Kai/sa")]
)
def test_entry_with_invalid_key_name_or_id_is_dropped(field: str, value: str) -> None:
    doc = _doc("16.17.1")
    doc["data"]["Kaisa"][field] = value
    catalog = parse_champion_catalog(
        json.dumps(doc).encode(), expected_version="16.17.1", locale="pt_BR"
    )
    assert 145 not in {e.champion_key for e in catalog.entries}


def test_duplicated_numeric_key_is_ambiguous_and_publishes_neither() -> None:
    doc = _doc("16.17.1")
    doc["data"]["Ezreal"]["key"] = "51"  # duas entradas com key 51
    catalog = parse_champion_catalog(
        json.dumps(doc).encode(), expected_version="16.17.1", locale="pt_BR"
    )
    assert 51 not in {e.champion_key for e in catalog.entries}
    assert 81 not in {e.champion_key for e in catalog.entries}
