"""Fingerprint da sentinela (CC-801) com proveniência de domínio patch × fila (CC-802)."""

from __future__ import annotations

import hashlib
import json

from riftlab.timeline.health import fingerprint

PATCH_WIDE = [
    {"ruleId": "R1", "ruleVersion": 1, "application": "INHERITED", "appliedFromPatch": "16.19"},
    {
        "ruleId": "R4s",
        "ruleVersion": 1,
        "application": "INHERITED",
        "appliedFromPatch": "16.19",
        "systemItemIds": [1201, 1202],
    },
]


def test_patch_wide_provenance_keeps_the_v11_fingerprint() -> None:
    # Forma calculada antes do CC-802: grupos de saúde existentes continuam casando.
    legacy = sorted(
        (r["ruleId"], r["ruleVersion"], r["application"], r["appliedFromPatch"], ())
        if r["ruleId"] != "R4s"
        else (r["ruleId"], r["ruleVersion"], r["application"], r["appliedFromPatch"], (1201, 1202))
        for r in PATCH_WIDE
    )
    expected = hashlib.sha256(json.dumps(legacy, separators=(",", ":")).encode()).hexdigest()
    assert fingerprint(PATCH_WIDE) == expected


def test_domain_queue_and_context_split_health_groups() -> None:
    solo = [
        {**r, "application": "VALIDATED", "appliedFromPatch": "16.20", "appliedFromQueue": 420}
        | {"catalogContext": "FULL_CANONICAL_V1"}
        for r in PATCH_WIDE
    ]
    other_context = [{**r, "catalogContext": "EVENT_RECIPE_CLOSURE_V11"} for r in solo]
    assert fingerprint(solo) != fingerprint(PATCH_WIDE)
    assert fingerprint(solo) != fingerprint(other_context)
    withheld = [*solo, {"ruleId": "R8", "ruleVersion": 1, "application": "WITHHELD"}]
    assert fingerprint(withheld) == fingerprint(solo)
