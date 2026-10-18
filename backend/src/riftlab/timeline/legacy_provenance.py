"""Contexto público y cerrado para normalizar procedencia conocida de estados v10."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

LEGACY_SOURCE_COMMIT = "87cb5f07872b531e5a17aaf2ccd323a22bb0b95b"
LEGACY_SOURCE_SHA256 = "8101c648fdfe867b43c3a1adac91333dc0c3eecf20c5fcb180c33acca1eedc20"


@lru_cache(maxsize=1)
def legacy_v10_context() -> dict[str, Any]:
    """Lê o manifesto empacotado e valida seus hashes; não consulta Git nem YAML atual."""
    here = Path(__file__).resolve()
    candidates = (
        here.parents[3] / "configs" / "reconstruction-v10-legacy-context.json",
        here.parents[4] / "configs" / "reconstruction-v10-legacy-context.json",
    )
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise FileNotFoundError("manifesto público do contexto legado v10 ausente")
    context = json.loads(path.read_text(encoding="utf-8"))
    claimed_hash = context.pop("contextSha256", None)
    actual_hash = hashlib.sha256(
        json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        claimed_hash != actual_hash
        or context.get("sourceCommit") != LEGACY_SOURCE_COMMIT
        or context.get("sourceSha256") != LEGACY_SOURCE_SHA256
    ):
        raise ValueError("hash/identidade do manifesto legado v10 inválido")
    context["contextSha256"] = claimed_hash
    return context


def normalize_known_v10_provenance(
    provenance: list[dict[str, Any]],
    *,
    patch: str,
    reconstruction_version: int,
    has_catalog: bool,
) -> list[dict[str, Any]] | None:
    """Reconhece apenas o snapshot v10 versionado e repõe o parâmetro ausente em memória."""
    context = legacy_v10_context()
    if (
        reconstruction_version != context["reconstructionVersion"]
        or patch != context["patch"]
        or not has_catalog
    ):
        return None
    if provenance != context["legacyProvenance"]:
        return None
    normalized = [dict(rule) for rule in provenance]
    r4s = next((rule for rule in normalized if rule.get("ruleId") == "R4s"), None)
    if r4s is None or "systemItemIds" in r4s:
        return None
    r4s["systemItemIds"] = list(context["systemItemIds"])
    return normalized
