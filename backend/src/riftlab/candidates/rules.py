"""Load a reviewed item rule manifest; never infer rules from today's catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from riftlab.candidates.domain import CandidateInputError, ItemRules


def load_item_rules(path: Path) -> ItemRules:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "gamePatch",
        "curationRulesVersion",
        "excludedIds",
        "exclusiveGroups",
        "exclusiveCoverage",
        "uniqueItemIds",
        "highIdNamespaceExcluded",
        "evidence",
    }:
        raise CandidateInputError("ITEM_RULES_MALFORMED")
    excluded = payload["excludedIds"]
    groups = payload["exclusiveGroups"]
    if (
        not all(
            isinstance(payload[key], str) and payload[key]
            for key in ("version", "gamePatch", "curationRulesVersion", "evidence")
        )
        or not isinstance(excluded, list)
        or not isinstance(groups, list)
        or not all(
            isinstance(value, bool)
            for value in (
                payload["exclusiveCoverage"],
                payload["uniqueItemIds"],
                payload["highIdNamespaceExcluded"],
            )
        )
        or not all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in excluded
        )
        or not all(
            isinstance(group, list)
            and len(group) >= 2
            and all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in group
            )
            for group in groups
        )
    ):
        raise CandidateInputError("ITEM_RULES_MALFORMED")
    return ItemRules(
        version=payload["version"],
        game_patch=payload["gamePatch"],
        curation_rules_version=payload["curationRulesVersion"],
        excluded_ids=frozenset(excluded),
        exclusive_groups=tuple(frozenset(group) for group in groups),
        exclusive_coverage=payload["exclusiveCoverage"],
        unique_item_ids=payload["uniqueItemIds"],
        high_id_namespace_excluded=payload["highIdNamespaceExcluded"],
    )
