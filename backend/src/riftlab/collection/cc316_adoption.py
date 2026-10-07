"""CC-316-B: adopted attempt-4 partition, verified and written once.

Amendment CC-316-B closes CC-316 discovery and adopts the attempt-4 snapshot.
This module verifies that snapshot, derives a deterministic artifact with
per-ID digests and provenance, and loads it back pinned by SHA-256. Its only
loader returns the 20 exploration IDs, never confirmation IDs.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections import Counter
from pathlib import Path
from typing import cast

from riftlab.collection.cc316_discovery import (
    MAX_CANDIDATES,
    MAX_REQUESTS,
    TARGET_MAP,
    TARGET_PATCH,
    TARGET_QUEUE,
    DiscoveryError,
    _atomic_write_once,
)

SOURCE_SNAPSHOT_FILENAME = "partition-snapshot.json"
SOURCE_SNAPSHOT_RELATIVE_PATH = "collection/cc316/partition-snapshot.json"
SOURCE_SNAPSHOT_SHA256 = "22ed57a66fe55302b53be37e3bcb3b67efaa83acc639c8e6dd7650b2b2634053"
AMENDMENT_RELATIVE_PATH = "validation/sprint-3/CC-316/02-amendment-cc316-b-2026-10-07.md"
AMENDMENT_SHA256 = "825581abe2c46976f6daab10ec342e4a40dfd22f334be812ce3d94bfc382f42b"
ADOPTED_PARTITION_FILENAME = "partition-cc316b-adopted.json"
# Pinned after the one-time derivation; any other content fails closed.
ADOPTED_PARTITION_SHA256 = "35a448627a46360d24314e753b2a9e358f35141f611e5d0ce5714cc3083af573"
EXPLORATION_SIZE = 20

_HASH_RULE = "sha256(CC-316-partition-v1|matchId), ascending"
_MATCH_ID = re.compile(r"^BR1_[0-9]{1,20}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_TARGET = {
    "patch": TARGET_PATCH,
    "queueId": TARGET_QUEUE,
    "mapId": TARGET_MAP,
    "gameComplete": True,
}
_PRIVACY_FLAGS = {
    "participantPuuidsPersisted": False,
    "participantHmacsPersisted": False,
    "matchBodiesPersisted": False,
    "timelineFetched": False,
}
_SOURCE_BY_DEPTH = {
    0: "CONSENTED_SEED_MATCH_LIST",
    1: "PARTICIPANT_MATCH_LIST",
    2: "PARTICIPANT_MATCH_LIST",
}
_ASSIGNMENTS = ("EXPLORATION", "CONFIRMATION_POTENTIAL", "OVERLAP_EXCLUDED", "OUT_OF_SCOPE")
_VERIFICATION = {
    "sourceSnapshotSha256Verified": True,
    "categoriesDisjoint": True,
    "categoriesCoverUniverse": True,
    "explorationIsFirst20ByDigest": True,
    "listsSortedByDigest": True,
    "privacyFlagsFalse": True,
}
_UNIVERSE_DEFINITION = "ATTEMPT_4_ADMITTED_CANDIDATES"


def partition_digest(match_id: str) -> str:
    return hashlib.sha256(f"CC-316-partition-v1|{match_id}".encode()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DiscoveryError(f"partição CC-316-B inválida: {message}")


def _mapping(value: object, field: str) -> dict[str, object]:
    _require(isinstance(value, dict), f"{field} deve ser objeto")
    return cast(dict[str, object], value)


def _identifiers(value: object, field: str) -> list[str]:
    _require(isinstance(value, list), f"{field} deve ser lista")
    items = cast(list[object], value)
    _require(
        all(isinstance(item, str) and _MATCH_ID.fullmatch(item) for item in items),
        f"{field} contém ID fora do escopo",
    )
    ids = cast(list[str], items)
    _require(len(ids) == len(set(ids)), f"{field} contém duplicatas")
    return ids


def _count(value: object, field: str, *, maximum: int) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum,
        f"{field} fora do intervalo",
    )
    return cast(int, value)


def _digest_order(ids: list[str]) -> list[str]:
    return sorted(ids, key=lambda match_id: (partition_digest(match_id), match_id))


def _check_assignment(
    universe: set[str],
    exploration: list[str],
    confirmation: list[str],
    overlap: list[str],
    out_of_scope: list[str],
) -> None:
    groups = [set(exploration), set(confirmation), set(overlap), set(out_of_scope)]
    union = set[str]().union(*groups)
    _require(sum(len(group) for group in groups) == len(union), "categorias se sobrepõem")
    _require(union == universe, "categorias não cobrem exatamente o universo")
    eligible = _digest_order(exploration + confirmation + overlap)
    _require(len(exploration) == EXPLORATION_SIZE, "exploração deve ter 20 partidas")
    _require(
        exploration == eligible[:EXPLORATION_SIZE],
        "exploração não é o conjunto ordenado dos 20 menores digests",
    )
    tail = eligible[EXPLORATION_SIZE:]
    overlap_set = set(overlap)
    _require(
        overlap == [match_id for match_id in tail if match_id in overlap_set],
        "sobreposição fora da cauda ou da ordem por digest",
    )
    _require(
        confirmation == [match_id for match_id in tail if match_id not in overlap_set],
        "confirmação altera a ordem ou repõe exclusões",
    )


def canonical_json(artifact: dict[str, object]) -> bytes:
    return (json.dumps(artifact, indent=2, sort_keys=True) + "\n").encode("utf-8")


def derive_adopted_partition(
    snapshot_bytes: bytes,
    *,
    source_sha256: str = SOURCE_SNAPSHOT_SHA256,
    amendment_sha256: str = AMENDMENT_SHA256,
) -> dict[str, object]:
    """Verify the attempt-4 snapshot and build the CC-316-B artifact; fail closed."""
    _require(
        hashlib.sha256(snapshot_bytes).hexdigest() == source_sha256,
        "hash do snapshot fonte divergente",
    )
    try:
        snapshot = json.loads(snapshot_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DiscoveryError("partição CC-316-B inválida: snapshot fonte ilegível") from exc
    root = _mapping(snapshot, "raiz do snapshot fonte")
    _require(
        set(root)
        == {
            "createdAt",
            "discovery",
            "elapsedSeconds",
            "partition",
            "privacy",
            "protocol",
            "requests",
            "schemaVersion",
            "status",
            "target",
        },
        "campos de raiz do snapshot fonte divergentes",
    )
    _require(
        root["schemaVersion"] == 1
        and root["protocol"] == "CC-316-A"
        and root["status"] == "PARTITION_POTENTIAL_READY",
        "versão ou status do snapshot fonte",
    )
    _require(root["target"] == _TARGET, "alvo do snapshot fonte fora do protocolo")
    _require(root["privacy"] == _PRIVACY_FLAGS, "flags de privacidade do snapshot fonte")
    created_at = root["createdAt"]
    _require(isinstance(created_at, str), "createdAt do snapshot fonte inválido")
    requests = _count(root["requests"], "requests do snapshot fonte", maximum=MAX_REQUESTS)

    discovery = _mapping(root["discovery"], "discovery do snapshot fonte")
    _require(
        set(discovery)
        == {
            "maximumMatchesPerPlayer",
            "maximumParticipantHops",
            "metadataFetchedCount",
            "source",
            "sourceDepthByMatchId",
            "stopReason",
            "uniqueCandidateCount",
        },
        "campos de discovery do snapshot fonte divergentes",
    )
    _require(
        discovery["source"] == "CONSENTED_SEEDS_PLUS_PARTICIPANTS"
        and discovery["maximumParticipantHops"] == 2
        and discovery["maximumMatchesPerPlayer"] == 4,
        "fonte ou limites de discovery divergentes",
    )
    stop_reason = discovery["stopReason"]
    _require(stop_reason in (None, "MAX_CANDIDATES"), "stopReason fora da allowlist")
    depth_map = _mapping(discovery["sourceDepthByMatchId"], "sourceDepthByMatchId")
    _require(
        all(_MATCH_ID.fullmatch(match_id) for match_id in depth_map),
        "sourceDepthByMatchId contém ID fora do escopo",
    )
    _require(
        all(
            isinstance(depth, int) and not isinstance(depth, bool) and depth in _SOURCE_BY_DEPTH
            for depth in depth_map.values()
        ),
        "profundidade fora do protocolo",
    )
    depths = cast(dict[str, int], depth_map)
    universe = set(depths)
    _require(EXPLORATION_SIZE <= len(universe) <= MAX_CANDIDATES, "tamanho do universo")
    _require(
        discovery["uniqueCandidateCount"] == len(universe)
        and discovery["metadataFetchedCount"] == len(universe),
        "contagens de discovery divergentes",
    )

    partition = _mapping(root["partition"], "partition do snapshot fonte")
    _require(
        set(partition)
        == {
            "confirmationPotentialMatchIds",
            "exclusionCounts",
            "explorationMatchIds",
            "hashRule",
            "overlapExcludedMatchIds",
            "structuralExcludedMatchIds",
        },
        "campos de partition do snapshot fonte divergentes",
    )
    _require(partition["hashRule"] == _HASH_RULE, "regra de hash divergente")
    exploration = _identifiers(partition["explorationMatchIds"], "explorationMatchIds")
    confirmation = _identifiers(
        partition["confirmationPotentialMatchIds"], "confirmationPotentialMatchIds"
    )
    overlap = _identifiers(partition["overlapExcludedMatchIds"], "overlapExcludedMatchIds")
    excluded = _mapping(partition["structuralExcludedMatchIds"], "structuralExcludedMatchIds")
    _require(
        all(_MATCH_ID.fullmatch(match_id) for match_id in excluded)
        and all(reason == "OUT_OF_SCOPE" for reason in excluded.values()),
        "exclusão estrutural fora da allowlist",
    )
    out_of_scope = sorted(excluded)
    _require(
        partition["exclusionCounts"]
        == ({"OUT_OF_SCOPE": len(out_of_scope)} if out_of_scope else {}),
        "contagem de exclusões divergente",
    )
    _check_assignment(universe, exploration, confirmation, overlap, out_of_scope)

    assignment = {match_id: "EXPLORATION" for match_id in exploration}
    assignment.update({match_id: "CONFIRMATION_POTENTIAL" for match_id in confirmation})
    assignment.update({match_id: "OVERLAP_EXCLUDED" for match_id in overlap})
    assignment.update({match_id: "OUT_OF_SCOPE" for match_id in out_of_scope})
    artifact: dict[str, object] = {
        "schemaVersion": 1,
        "protocol": "CC-316-B",
        "status": "ADOPTED_PARTITION_READY",
        "discoveryClosed": True,
        "amendment": {"path": AMENDMENT_RELATIVE_PATH, "sha256": amendment_sha256},
        "sourceSnapshot": {
            "path": SOURCE_SNAPSHOT_RELATIVE_PATH,
            "sha256": source_sha256,
            "protocol": "CC-316-A",
            "attemptNumber": 4,
            "createdAt": created_at,
            "requests": requests,
        },
        "target": dict(_TARGET),
        "universe": {
            "definition": _UNIVERSE_DEFINITION,
            "candidateCount": len(universe),
            "eligibleCount": len(exploration) + len(confirmation) + len(overlap),
            "outOfScopeCount": len(out_of_scope),
            "depthCounts": {
                str(depth): amount for depth, amount in sorted(Counter(depths.values()).items())
            },
            "stopReason": stop_reason,
            "maximumMatchesPerPlayer": 4,
            "maximumParticipantHops": 2,
            "unadmittedListedIdsRecorded": False,
        },
        "candidates": {
            match_id: {
                "assignment": assignment[match_id],
                "depth": depths[match_id],
                "partitionDigest": partition_digest(match_id),
                "source": _SOURCE_BY_DEPTH[depths[match_id]],
            }
            for match_id in sorted(universe)
        },
        "partition": {
            "hashRule": _HASH_RULE,
            "explorationMatchIds": exploration,
            "confirmationPotentialMatchIds": confirmation,
            "overlapExcludedMatchIds": overlap,
            "outOfScopeMatchIds": out_of_scope,
        },
        "verification": dict(_VERIFICATION),
        "privacy": dict(_PRIVACY_FLAGS),
    }
    validate_adopted_partition(artifact)
    return artifact


def validate_adopted_partition(artifact: object) -> tuple[str, ...]:
    """Re-check a derived artifact end to end; return only its exploration IDs.

    The artifact's own pinned SHA-256 binds the source snapshot and amendment
    hashes it records; here they only need the expected shape.
    """
    root = _mapping(artifact, "raiz")
    _require(
        set(root)
        == {
            "schemaVersion",
            "protocol",
            "status",
            "discoveryClosed",
            "amendment",
            "sourceSnapshot",
            "target",
            "universe",
            "candidates",
            "partition",
            "verification",
            "privacy",
        },
        "campos de raiz divergentes",
    )
    _require(
        root["schemaVersion"] == 1
        and root["protocol"] == "CC-316-B"
        and root["status"] == "ADOPTED_PARTITION_READY"
        and root["discoveryClosed"] is True,
        "versão, status ou encerramento da descoberta",
    )
    amendment = _mapping(root["amendment"], "amendment")
    _require(
        set(amendment) == {"path", "sha256"}
        and amendment["path"] == AMENDMENT_RELATIVE_PATH
        and isinstance(amendment["sha256"], str)
        and bool(_SHA256_HEX.fullmatch(amendment["sha256"])),
        "emenda divergente",
    )
    source = _mapping(root["sourceSnapshot"], "sourceSnapshot")
    _require(
        set(source) == {"path", "sha256", "protocol", "attemptNumber", "createdAt", "requests"}
        and source["path"] == SOURCE_SNAPSHOT_RELATIVE_PATH
        and isinstance(source["sha256"], str)
        and bool(_SHA256_HEX.fullmatch(source["sha256"]))
        and source["protocol"] == "CC-316-A"
        and source["attemptNumber"] == 4
        and isinstance(source["createdAt"], str),
        "snapshot fonte divergente",
    )
    _count(source["requests"], "requests do snapshot fonte", maximum=MAX_REQUESTS)
    _require(root["target"] == _TARGET, "alvo fora do protocolo")
    _require(root["privacy"] == _PRIVACY_FLAGS, "flags de privacidade")
    _require(root["verification"] == _VERIFICATION, "verificações divergentes")

    partition = _mapping(root["partition"], "partition")
    _require(
        set(partition)
        == {
            "hashRule",
            "explorationMatchIds",
            "confirmationPotentialMatchIds",
            "overlapExcludedMatchIds",
            "outOfScopeMatchIds",
        },
        "campos de partition divergentes",
    )
    _require(partition["hashRule"] == _HASH_RULE, "regra de hash divergente")
    exploration = _identifiers(partition["explorationMatchIds"], "explorationMatchIds")
    confirmation = _identifiers(
        partition["confirmationPotentialMatchIds"], "confirmationPotentialMatchIds"
    )
    overlap = _identifiers(partition["overlapExcludedMatchIds"], "overlapExcludedMatchIds")
    out_of_scope = _identifiers(partition["outOfScopeMatchIds"], "outOfScopeMatchIds")
    _require(out_of_scope == sorted(out_of_scope), "outOfScopeMatchIds fora da ordem canônica")

    candidates = _mapping(root["candidates"], "candidates")
    _require(
        all(_MATCH_ID.fullmatch(match_id) for match_id in candidates),
        "candidates contém ID fora do escopo",
    )
    universe = set(candidates)
    _require(EXPLORATION_SIZE <= len(universe) <= MAX_CANDIDATES, "tamanho do universo")
    _check_assignment(universe, exploration, confirmation, overlap, out_of_scope)
    expected_assignment = {match_id: "EXPLORATION" for match_id in exploration}
    expected_assignment.update({match_id: "CONFIRMATION_POTENTIAL" for match_id in confirmation})
    expected_assignment.update({match_id: "OVERLAP_EXCLUDED" for match_id in overlap})
    expected_assignment.update({match_id: "OUT_OF_SCOPE" for match_id in out_of_scope})
    depth_counts: Counter[int] = Counter()
    for match_id, value in candidates.items():
        record = _mapping(value, "candidato")
        depth = record.get("depth")
        _require(
            set(record) == {"assignment", "depth", "partitionDigest", "source"}
            and isinstance(depth, int)
            and not isinstance(depth, bool)
            and depth in _SOURCE_BY_DEPTH
            and record["source"] == _SOURCE_BY_DEPTH[depth]
            and record["partitionDigest"] == partition_digest(match_id)
            and record["assignment"] in _ASSIGNMENTS
            and record["assignment"] == expected_assignment[match_id],
            "registro de candidato incoerente",
        )
        depth_counts[cast(int, depth)] += 1

    universe_info = _mapping(root["universe"], "universe")
    _require(
        universe_info
        == {
            "definition": _UNIVERSE_DEFINITION,
            "candidateCount": len(universe),
            "eligibleCount": len(exploration) + len(confirmation) + len(overlap),
            "outOfScopeCount": len(out_of_scope),
            "depthCounts": {str(depth): amount for depth, amount in sorted(depth_counts.items())},
            "stopReason": universe_info.get("stopReason"),
            "maximumMatchesPerPlayer": 4,
            "maximumParticipantHops": 2,
            "unadmittedListedIdsRecorded": False,
        }
        and universe_info.get("stopReason") in (None, "MAX_CANDIDATES"),
        "resumo do universo divergente",
    )
    return tuple(exploration)


def persist_adopted_partition_once(path: Path, artifact: dict[str, object]) -> str:
    """Validate before encoding; write the artifact and its sidecar exactly once."""
    validate_adopted_partition(artifact)
    if path.exists() or path.with_suffix(path.suffix + ".sha256").exists():
        raise DiscoveryError("partição adotada já existe; artefato imutável não será sobrescrito")
    return _atomic_write_once(path, canonical_json(artifact))


def read_private_file(path: Path, field: str) -> bytes:
    try:
        file_stat = path.lstat()
        parent_stat = path.parent.stat()
    except OSError as exc:
        raise DiscoveryError(f"{field} ausente ou inacessível") from exc
    if (
        not any(part == ".private" for part in path.parts)
        or not stat.S_ISREG(file_stat.st_mode)
        or stat.S_IMODE(file_stat.st_mode) != 0o600
        or stat.S_IMODE(parent_stat.st_mode) & 0o077
    ):
        raise DiscoveryError(f"{field} inseguro; esperado arquivo 0600 em diretório privado")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise DiscoveryError(f"{field} ilegível") from exc


def read_private_with_sidecar(path: Path, field: str) -> tuple[bytes, str]:
    raw = read_private_file(path, field)
    sidecar = read_private_file(path.with_suffix(path.suffix + ".sha256"), f"sidecar de {field}")
    digest = hashlib.sha256(raw).hexdigest()
    if sidecar != f"{digest}  {path.name}\n".encode("ascii"):
        raise DiscoveryError(f"sidecar SHA-256 de {field} divergente")
    return raw, digest


def private_root_for(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.name == ".private":
            return candidate
    raise DiscoveryError("caminho fora de .private")


def load_adopted_exploration_ids(
    path: Path, *, expected_sha256: str = ADOPTED_PARTITION_SHA256
) -> tuple[str, ...]:
    """Return the 20 exploration IDs of the pinned adopted artifact, never others."""
    raw, digest = read_private_with_sidecar(path, "partição adotada")
    if not _SHA256_HEX.fullmatch(expected_sha256) or digest != expected_sha256:
        raise DiscoveryError("hash da partição adotada diverge do valor fixado no código")
    try:
        artifact = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DiscoveryError("partição adotada ilegível") from exc
    _require(
        isinstance(artifact, dict) and raw == canonical_json(artifact), "serialização não canônica"
    )
    return validate_adopted_partition(artifact)


def verify_amendment(private_root: Path, *, expected_sha256: str = AMENDMENT_SHA256) -> None:
    """Fail closed unless the CC-316-B amendment and its sidecar are unchanged."""
    path = private_root / AMENDMENT_RELATIVE_PATH
    try:
        raw = path.read_bytes()
        sidecar = path.with_suffix(path.suffix + ".sha256").read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise DiscoveryError("emenda CC-316-B ausente ou ilegível") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256 or sidecar != f"{digest}  {path.name}\n":
        raise DiscoveryError("emenda CC-316-B ou sidecar divergente do valor fixado")
