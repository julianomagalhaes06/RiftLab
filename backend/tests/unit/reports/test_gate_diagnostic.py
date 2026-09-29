from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from riftlab.reports.gate_diagnostic import (
    GateDiagnosticError,
    _intersection_bounds,
    diagnose_frozen_gate,
)


def _item(name: str, *, recipe: list[str], into: list[str]) -> dict[str, object]:
    return {
        "name": name,
        "gold": {"total": 300, "purchasable": True},
        "maps": {"11": True},
        "tags": [],
        "stats": {},
        "from": recipe,
        "into": into,
    }


def _part(matches: int, total: int, inventory: int, scope: int, ordinal: int) -> dict:
    return {
        "matchesImported": matches,
        "states": {"total": total * 2, "preDecisionUncertain": inventory},
        "points": {"total": total, "eligible": 0, "ineligible": total},
        "inRecorte": {
            "points": ordinal,
            "eligible": 0,
            "reasons": {"INVENTORY_UNCERTAIN": ordinal, "OUT_OF_SCOPE": ordinal},
            "subReasons": {"OUT_OF_SCOPE:MAIN_ITEM_ORDINAL_BEYOND_SECOND": ordinal},
        },
        "reasons": {"INVENTORY_UNCERTAIN": inventory, "OUT_OF_SCOPE": scope},
        "subReasons": {},
        "byPatch": {},
    }


def _write_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    frozen = root / "frozen"
    catalog = root / "catalog"
    rules = root / "rules"
    for path in (frozen, catalog, rules):
        path.mkdir()
    all_part = _part(2, 5, 4, 3, 2)
    all_part["byPatch"] = {
        "16.16": {"matches": 1, "points": 2, "eligible": 0},
        "16.17": {"matches": 1, "points": 3, "eligible": 0},
    }
    exploration = _part(1, 2, 2, 1, 1)
    confirmation = _part(1, 3, 2, 2, 1)
    for version, filename in (
        (6, "51-remeasure-before-v6.json"),
        (7, "53-remeasure-after-v7.json"),
    ):
        (frozen / filename).write_text(
            json.dumps(
                {
                    "reconstructionVersion": version,
                    "sets": {
                        "todas-REAL": all_part,
                        "exploracao": exploration,
                        "confirmacao": confirmation,
                    },
                }
            )
        )
    (frozen / "50-confirmation-result.json").write_text(
        json.dumps(
            {
                "partition": "confirmation",
                "rows": {
                    f"G-FINAL|{patch}": {
                        "k": 0,
                        "n": 2,
                        "nMin": 20,
                        "mMin": 3,
                        "matchesContributing": 1,
                        "wilsonUpper": 0.6576,
                        "epsilon": 0.1,
                        "outcome": "NO_SAMPLE",
                    }
                    for patch in ("16.16", "16.17")
                },
                "matchLabels": {"BR1_SENTINEL_PRIVATE": "P01"},
                "subject_hash": "HMAC_SENTINEL_PRIVATE",
                "puuid": "PUUID_SENTINEL_PRIVATE",
            }
        )
    )
    mapping = root / "mapping.yaml"
    mapping.write_text(
        "schemaVersion: v1\nmappingVersion: v1\nmappings:\n"
        '  "16.16":\n    dataDragonVersion: "16.16.1"\n'
        '  "16.17":\n    dataDragonVersion: "16.17.1"\n'
    )
    for patch in ("16.16", "16.17"):
        (catalog / f"item-{patch}.1.json").write_text(
            json.dumps(
                {
                    "type": "item",
                    "version": f"{patch}.1",
                    "groups": [],
                    "data": {
                        "1": _item("PLAYER_SENTINEL_PRIVATE", recipe=[], into=["100"]),
                        "100": _item("ITEM_SENTINEL_PRIVATE", recipe=["1"], into=[]),
                    },
                }
            )
        )
        (rules / f"{patch}.json").write_text(
            json.dumps(
                {
                    "version": "fixture_v1",
                    "gamePatch": patch,
                    "curationRulesVersion": "data_dragon_v5",
                    "excludedIds": [],
                    "exclusiveGroups": [],
                    "exclusiveCoverage": False,
                    "uniqueItemIds": False,
                    "highIdNamespaceExcluded": True,
                    "evidence": "fixture",
                }
            )
        )
    return frozen, catalog, rules, mapping


def test_bounds_do_not_invent_intersection() -> None:
    assert _intersection_bounds(5, 4, 3) == {"min": 2, "max": 3, "exact": False}
    assert _intersection_bounds(5, 5, 3) == {"min": 3, "max": 3, "exact": True}


def test_offline_diagnostic_only_reads_and_redacts_identifiers(tmp_path: Path) -> None:
    inputs = _write_fixture(tmp_path)
    source_files = [file for directory in inputs[:3] for file in directory.iterdir()]
    source_files.append(inputs[3])
    before = {str(file): hashlib.sha256(file.read_bytes()).hexdigest() for file in source_files}
    result = diagnose_frozen_gate(*inputs)
    after = {str(file): hashlib.sha256(file.read_bytes()).hexdigest() for file in source_files}
    assert before == after
    assert result["intersections"]["inventoryAndOutOfScopeAll"] == {
        "min": 2,
        "max": 3,
        "exact": False,
    }
    assert result["intersections"]["inventoryAndOrdinalInScope"]["min"] == 2
    assert result["catalogs"]["16.16"]["legalCandidateCountWithCurrentManifest"] == 0
    output = json.dumps(result)
    for secret in (
        "BR1_SENTINEL_PRIVATE",
        "HMAC_SENTINEL_PRIVATE",
        "PUUID_SENTINEL_PRIVATE",
        "PLAYER_SENTINEL_PRIVATE",
        "ITEM_SENTINEL_PRIVATE",
    ):
        assert secret not in output


def test_mismatched_version_and_partition_fail_closed(tmp_path: Path) -> None:
    inputs = _write_fixture(tmp_path)
    catalog = inputs[1] / "item-16.16.1.json"
    document = json.loads(catalog.read_text())
    document["version"] = "16.17.1"
    catalog.write_text(json.dumps(document))
    with pytest.raises(GateDiagnosticError, match="DATA_DRAGON_VERSION_MISMATCH"):
        diagnose_frozen_gate(*inputs)
    document["version"] = "16.16.1"
    catalog.write_text(json.dumps(document))
    frozen = inputs[0] / "53-remeasure-after-v7.json"
    report = json.loads(frozen.read_text())
    report["sets"]["todas-REAL"]["points"]["total"] = 6
    frozen.write_text(json.dumps(report))
    with pytest.raises(GateDiagnosticError, match="PARTICAO_INCONCILIAVEL"):
        diagnose_frozen_gate(*inputs)
