"""Resolução pura de popularidade herdada, condicionada à identidade do catálogo."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from riftlab.catalog.classification import ItemClassificationTable
from riftlab.catalog.diff import catalog_diff
from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.ranking.engine import Popularity
from riftlab.shared.numeric import is_ascii_digits, patch_order_key


@dataclass(frozen=True, slots=True)
class PopularityCatalog:
    catalog: CatalogVersion
    items: Mapping[int, Item]
    classification: ItemClassificationTable


@dataclass(frozen=True, slots=True)
class InheritedPopularity:
    popularity: Popularity | None
    support: int
    reason_code: str | None
    source_patch: str
    source_version: str
    source_dataset_sha256: str | None
    source_catalog_id: str | None = None
    source_catalog_sha256: str | None = None


_IDENTITY_FIELDS = {
    "gamePatch",
    "dataDragonVersion",
    "sha256",
    "mappingVersion",
    "rawHash",
    "curationRulesVersion",
}
_IDENTITY_OPTIONAL_FIELDS = {"catalogId"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"_v([0-9]+)$")


def latest_previous_artifact(configs: Path, target_patch: str) -> Popularity | None:
    """Escolhe o artefato mais recente por patch numérico anterior; não tenta ancestrais."""
    target_key = patch_order_key(target_patch)
    if target_key is None:
        return None
    candidates: list[tuple[tuple[int, int], int, Popularity]] = []
    for path in (configs / "ranking").glob("popularity-*.json"):
        try:
            artifact_bytes = path.read_bytes()
            document = json.loads(artifact_bytes)
            patch = document.get("gamePatch")
            version = document.get("version")
            cells = document.get("cells")
        except (OSError, json.JSONDecodeError):
            continue
        key = patch_order_key(patch) if isinstance(patch, str) else None
        if (
            key is None
            or key >= target_key
            or not isinstance(version, str)
            or not isinstance(cells, dict)
        ):
            continue
        version_match = _VERSION.search(version)
        version_order = int(version_match.group(1)) if version_match else 0
        popularity = Popularity.load_document(document)
        # A companion must bind both immutable inputs. The historical artifact
        # itself stays byte-for-byte untouched.
        companion = path.with_name(f"{path.stem}.provenance.json")
        try:
            manifest = json.loads(companion.read_bytes())
        except (OSError, json.JSONDecodeError):
            manifest = None
        if (
            isinstance(manifest, dict)
            and manifest.get("schemaVersion") == 1
            and manifest.get("artifactSha256") == hashlib.sha256(artifact_bytes).hexdigest()
            and manifest.get("datasetSha256") == popularity.dataset_sha256
            and isinstance(manifest.get("sourceCatalogIdentity"), dict)
        ):
            popularity = Popularity(
                popularity.version,
                popularity.cells,
                popularity.dataset_sha256,
                manifest["sourceCatalogIdentity"],
                popularity.game_patch,
            )
        candidates.append((key, version_order, popularity))
    if not candidates:
        return None
    return max(candidates, key=lambda row: (row[0], row[1]))[2]


def _identity_matches(identity: Any, catalog: CatalogVersion) -> bool:
    if (
        not isinstance(identity, Mapping)
        or not _IDENTITY_FIELDS.issubset(identity)
        or set(identity) - _IDENTITY_FIELDS - _IDENTITY_OPTIONAL_FIELDS
    ):
        return False
    return (
        all(
            identity.get(field) == expected
            for field, expected in (
                ("gamePatch", catalog.game_patch),
                ("dataDragonVersion", catalog.data_dragon_version),
                ("sha256", catalog.sha256),
                ("mappingVersion", catalog.mapping_version),
                ("rawHash", catalog.raw_hash),
                ("curationRulesVersion", catalog.curation_rules_version),
            )
        )
        and _SHA256.fullmatch(str(identity.get("sha256", ""))) is not None
    )


def filter_inherited_popularity(
    artifact: Popularity,
    source: PopularityCatalog | None,
    destination: PopularityCatalog,
) -> InheritedPopularity:
    """Filtra cada célula por diff estrutural e existência no destino, sem redistribuir."""
    identity = artifact.source_catalog_identity
    source_patch = artifact.game_patch or "UNKNOWN"
    identity_id = identity.get("catalogId") if isinstance(identity, Mapping) else None
    identity_sha = identity.get("sha256") if isinstance(identity, Mapping) else None

    def unavailable(reason_code: str) -> InheritedPopularity:
        return InheritedPopularity(
            popularity=None,
            support=0,
            reason_code=reason_code,
            source_patch=source_patch,
            source_version=artifact.version,
            source_dataset_sha256=artifact.dataset_sha256,
            source_catalog_id=identity_id if isinstance(identity_id, str) else None,
            source_catalog_sha256=identity_sha if isinstance(identity_sha, str) else None,
        )

    if identity is None:
        return unavailable("POPULARITY_SOURCE_CATALOG_IDENTITY_UNBOUND")
    if (
        not isinstance(identity, Mapping)
        or not _IDENTITY_FIELDS.issubset(identity)
        or set(identity) - _IDENTITY_FIELDS - _IDENTITY_OPTIONAL_FIELDS
        or not all(
            isinstance(identity.get(field), str) and identity.get(field)
            for field in _IDENTITY_FIELDS
        )
        or _SHA256.fullmatch(str(identity.get("sha256", ""))) is None
        or not _SHA256.fullmatch(str(identity.get("rawHash", "")))
        or (
            identity.get("catalogId") is not None and not isinstance(identity.get("catalogId"), str)
        )
    ):
        return unavailable("POPULARITY_SOURCE_CATALOG_IDENTITY_INVALID")
    if source is None:
        return unavailable("POPULARITY_SOURCE_CATALOG_NOT_FOUND")
    source_key = patch_order_key(source.catalog.game_patch)
    destination_key = patch_order_key(destination.catalog.game_patch)
    if source_key is None or destination_key is None or source_key >= destination_key:
        return unavailable("POPULARITY_SOURCE_PATCH_NOT_PREVIOUS")
    if (
        artifact.game_patch != source.catalog.game_patch
        or identity.get("gamePatch") != source.catalog.game_patch
    ):
        return unavailable("POPULARITY_SOURCE_PATCH_MISMATCH")
    if not _identity_matches(identity, source.catalog):
        return unavailable("POPULARITY_SOURCE_CATALOG_IDENTITY_MISMATCH")
    if any(item.catalog_id != source.catalog.id for item in source.items.values()):
        return unavailable("POPULARITY_SOURCE_ITEMS_IDENTITY_MISMATCH")
    if any(item.catalog_id != destination.catalog.id for item in destination.items.values()):
        return unavailable("POPULARITY_DESTINATION_ITEMS_IDENTITY_MISMATCH")

    changed = catalog_diff(
        source.items,
        destination.items,
        source.classification,
        destination.classification,
    )
    filtered: dict[str, dict[str, int]] = {}
    for cell_key, counts in artifact.cells.items():
        if not isinstance(cell_key, str) or not isinstance(counts, Mapping):
            return unavailable("POPULARITY_SOURCE_ARTIFACT_INVALID")
        parts = cell_key.split("|")
        if len(parts) != 2 or not all(is_ascii_digits(part) for part in parts):
            return unavailable("POPULARITY_SOURCE_ARTIFACT_INVALID")
        kept: dict[str, int] = {}
        for raw_item_id, count in counts.items():
            if (
                not is_ascii_digits(raw_item_id)
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
            ):
                return unavailable("POPULARITY_SOURCE_ARTIFACT_INVALID")
            item_id = int(raw_item_id)
            if (
                item_id not in source.items
                or item_id not in destination.items
                or item_id in changed
            ):
                continue
            if count > 0:
                kept[raw_item_id] = count
        if kept:
            filtered[cell_key] = kept
    support = sum(sum(counts.values()) for counts in filtered.values())
    # The historical B0 contract uses support > 0 (supportThreshold=0); it is
    # intentionally not replaced by the >=20 threshold reserved for CC-803 Part B.
    resolved = Popularity(
        artifact.version,
        filtered,
        artifact.dataset_sha256,
        identity,
        artifact.game_patch,
    )
    return InheritedPopularity(
        resolved,
        support=support,
        reason_code=None,
        source_patch=source.catalog.game_patch,
        source_version=artifact.version,
        source_dataset_sha256=artifact.dataset_sha256,
        source_catalog_id=str(source.catalog.id),
        source_catalog_sha256=source.catalog.sha256,
    )
