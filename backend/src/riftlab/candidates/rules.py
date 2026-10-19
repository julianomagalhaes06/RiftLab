"""Load a reviewed item rule manifest; never infer rules from today's catalog.

A patch without its own manifest inherits the latest EARLIER manifest that proves full
coverage (exclusive groups and unique ids, with the covered items listed). The
inherited rules keep the source's groups, record where they came from, and fail closed
on every main item of the new patch the source does not cover."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from riftlab.candidates.domain import CandidateInputError, ItemRules
from riftlab.shared.numeric import patch_order_key

_REQUIRED_KEYS = frozenset(
    {
        "version",
        "gamePatch",
        "curationRulesVersion",
        "excludedIds",
        "exclusiveGroups",
        "exclusiveCoverage",
        "uniqueItemIds",
        "highIdNamespaceExcluded",
        "evidence",
    }
)


def load_item_rules(path: Path) -> ItemRules:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not (
        set(payload) == _REQUIRED_KEYS or set(payload) == _REQUIRED_KEYS | {"coveredItemIds"}
    ):
        raise CandidateInputError("ITEM_RULES_MALFORMED")
    covered = payload.get("coveredItemIds")
    if covered is not None and not (
        isinstance(covered, list)
        and covered
        and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in covered)
        and len(set(covered)) == len(covered)
    ):
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
        covered_item_ids=frozenset(covered) if covered is not None else None,
    )


def _inheritable(rules: ItemRules) -> bool:
    return bool(rules.exclusive_coverage and rules.unique_item_ids and rules.covered_item_ids)


def resolve_item_rules(
    directory: Path,
    patch: str,
    main_item_ids: Iterable[int],
    *,
    changed_item_ids: Iterable[int] = (),
) -> ItemRules | None:
    """The patch's own manifest, else the inherited one, else None (unsupported).
    `main_item_ids` are the MAIN items of the patch's catalog: any not covered by the
    source manifest gets an unknown exclusivity."""
    own = directory / f"{patch}.json"
    if own.exists():
        return load_item_rules(own)
    key = patch_order_key(patch)
    if key is None:
        return None
    earlier = sorted(
        (
            (source_key, path)
            for path in directory.glob("*.json")
            if (source_key := patch_order_key(path.stem)) is not None and source_key < key
        ),
        reverse=True,
    )
    for _, path in earlier:
        source = load_item_rules(path)
        if not _inheritable(source):
            continue
        assert source.covered_item_ids is not None
        return replace(
            source,
            version=f"{source.version}+inherited_{patch}",
            game_patch=patch,
            inherited_from=source.game_patch,
            uncovered_item_ids=(frozenset(main_item_ids) - source.covered_item_ids)
            | frozenset(changed_item_ids),
        )
    return None


async def resolve_catalog_item_rules(
    session: Any, directory: Path, bundle: Any
) -> ItemRules | None:
    """Item rules for the patch of `bundle` (a `dataset.rows._catalog` bundle), as the
    analysis applies them: own manifest, else the inherited one with every main item of
    the patch that the source does not cover, or that changed structurally against the
    source patch's catalog, marked uncovered. None when unsupported or the source catalog
    is missing (fail closed)."""
    from riftlab.catalog.diff import catalog_diff, patch_catalog

    def main_ids(target: Any) -> set[int]:
        return {
            item.item_id
            for item in target.items
            if target.classification.classify(item.item_id).item_class.value == "MAIN"
        }

    patch = bundle.catalog.game_patch
    rules = resolve_item_rules(directory, patch, main_ids(bundle))
    if rules is None or not rules.inherited_from:
        return rules
    source = await patch_catalog(session, rules.inherited_from)
    if source is None:
        return None
    changes = catalog_diff(
        {i.item_id: i for i in source.items},
        {i.item_id: i for i in bundle.items},
        source.classification,
        bundle.classification,
    )
    changed_main = set(changes) & (main_ids(bundle) | main_ids(source))
    return resolve_item_rules(directory, patch, main_ids(bundle), changed_item_ids=changed_main)
