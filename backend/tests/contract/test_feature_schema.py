"""configs/feature-schema-v1.json (CC-204) contra a allowlist e as
especificações do código: o schema e a implementação não podem divergir.
Também trava os nomes proibidos e o recorte real (configs/scope.yaml)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from riftlab.decisions.domain import FEATURE_SCHEMA_VERSION
from riftlab.decisions.features import (
    FEATURE_ALLOWLIST,
    FEATURE_SPECS,
    FORBIDDEN_FIELD_NAMES,
)
from riftlab.decisions.scope import load_real_scope
from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.application import STALE_FRAME_THRESHOLD_MS


def _config_path(name: str) -> Path:
    """No container os configs vivem em /app/configs (Dockerfile); no host, na
    raiz do repositório. Ausência nos dois é falha, não skip: os arquivos são
    copiados para a imagem justamente para estes testes rodarem lá."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] / "configs" / name, here.parents[3] / "configs" / name):
        if candidate.is_file():
            return candidate
    pytest.fail(f"configs/{name} não encontrado nem no container nem no repositório")


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return json.loads(_config_path("feature-schema-v1.json").read_text(encoding="utf-8"))


def test_schema_version_matches_code(schema: dict[str, Any]) -> None:
    assert schema["featureSchemaVersion"] == FEATURE_SCHEMA_VERSION == "feature_schema_v1"
    assert schema["staleFrameThresholdMs"] == STALE_FRAME_THRESHOLD_MS == 90_000


def test_schema_fields_equal_code_allowlist_in_order(schema: dict[str, Any]) -> None:
    assert [field["name"] for field in schema["fields"]] == list(FEATURE_ALLOWLIST)


def test_schema_field_definitions_equal_code_specs(schema: dict[str, Any]) -> None:
    by_name = {field["name"]: field for field in schema["fields"]}
    for spec in FEATURE_SPECS:
        field = by_name[spec.name]
        assert field["type"] == spec.dtype, spec.name
        assert field["nullable"] is spec.nullable, spec.name
        assert field["provenance"] == spec.provenance, spec.name
        assert field["observability"] == spec.observability, spec.name
        assert field["masks"] == list(spec.masks), spec.name


def test_every_field_declares_type_nullability_provenance_observability_and_mask(
    schema: dict[str, Any],
) -> None:
    valid_masks = set(schema["maskValues"])
    for field in schema["fields"]:
        assert set(field) == {"name", "type", "nullable", "provenance", "observability", "masks"}
        assert field["provenance"] and field["observability"]
        assert field["masks"] and set(field["masks"]) <= valid_masks
        if field["nullable"] and field["name"] != _EMPTY_SLOT_FIELD:
            assert set(field["masks"]) - {"PRESENT"}, (
                f"{field['name']}: nulo sem máscara de ausência"
            )


# Único campo nulo sem máscara de ausência: null = slot vazio, um estado
# CONHECIDO do inventário (não uma observação faltante); a proveniência diz isso.
_EMPTY_SLOT_FIELD = "inventory_trinket_item_id"


def test_the_only_null_without_absence_mask_is_the_documented_empty_trinket_slot(
    schema: dict[str, Any],
) -> None:
    by_name = {field["name"]: field for field in schema["fields"]}
    assert "slot vazio" in by_name[_EMPTY_SLOT_FIELD]["provenance"]
    unmasked = [
        f["name"] for f in schema["fields"] if f["nullable"] and set(f["masks"]) == {"PRESENT"}
    ]
    assert unmasked == [_EMPTY_SLOT_FIELD]


def test_forbidden_names_equal_code_and_no_field_is_forbidden(schema: dict[str, Any]) -> None:
    assert set(schema["forbiddenFieldNames"]) == set(FORBIDDEN_FIELD_NAMES)
    assert not set(FEATURE_ALLOWLIST) & FORBIDDEN_FIELD_NAMES
    forbidden_tokens = {"puuid", "hash", "win", "outcome", "result", "kills", "deaths", "damage"}
    for name in FEATURE_ALLOWLIST:
        assert not set(name.split("_")) & forbidden_tokens, name


def test_composition_aggregates_are_declared_unavailable(schema: dict[str, Any]) -> None:
    by_name = {field["name"]: field for field in schema["fields"]}
    for name in ("ally_composition_aggregates", "enemy_composition_aggregates"):
        assert by_name[name]["masks"] == ["UNAVAILABLE_IN_SCHEMA"]
        assert by_name[name]["nullable"] is True


def test_real_scope_yaml_is_parsed_without_being_changed() -> None:
    scope = load_real_scope(_config_path("scope.yaml"), ("16.19", "16.18", "16.17"))
    assert scope.origin is SourceOrigin.COMPLETED_MATCH
    assert (scope.queue, scope.map_id, scope.role) == (420, 11, "BOTTOM")
    assert scope.champion_ids == frozenset({145, 51, 81, 222, 202, 804, 18, 22, 21, 29})
    assert scope.patches == frozenset({"16.19", "16.18"})
    assert scope.version == "scope_yaml:v2:2026-10-14:16.19,16.18"
