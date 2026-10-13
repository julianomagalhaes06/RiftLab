"""CC-405: model registry — trusted local artifacts, atomic promotion, rollback, fallback.

Only files under an allowed local root (the private artifacts directory) can be
registered; the SHA-256 is recorded at registration and re-checked at promotion
and at load. Promotions for one (task, scope) are serialised by an advisory lock
and the partial unique index allows a single ACTIVE version. Nothing is ever
deleted: retired versions and every event stay for historical reports. Without
an active version, the caller gets an explicit baseline fallback with a reason.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.ml.models import (
    DatasetManifestMatchModel,
    DatasetManifestModel,
    ModelEventModel,
    ModelVersionModel,
)


class RegistryError(RuntimeError):
    """Refused registry operation (bad hash, untrusted path, unknown version)."""


@dataclass(frozen=True, slots=True)
class ActiveVersion:
    version_id: uuid.UUID | None
    kind: str
    name: str
    artifact_path: str | None
    artifact_sha256: str | None
    fallback_reason: str | None = None


_LOADED: dict[tuple[str, str], ActiveVersion] = {}


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RegistryError("artefato ausente ou ilegível") from exc


def _trusted(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise RegistryError("artefato fora do diretório local confiável")
    return resolved


def dataset_match_ids(path: Path) -> set[str] | None:
    """Partidas listadas no artefato (linhas com `matchId` ou listas de partidas por split).

    None quando o formato não permite saber; o expurgo trata esse dataset como não
    verificável em vez de supor que ele não contém a partida (CC-603).
    """
    try:
        document = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    found: set[str] = set()
    if isinstance(document, dict):
        for row in document.get("rows") or []:
            if isinstance(row, dict) and isinstance(row.get("matchId"), str):
                found.add(row["matchId"])
        for listing in (document.get("splits") or {}).values():
            if isinstance(listing, list):
                found.update(m for m in listing if isinstance(m, str))
        if isinstance(document.get("matches"), list):
            found.update(m for m in document["matches"] if isinstance(m, str))
    return found or None


async def register_dataset(
    session_factory: async_sessionmaker[AsyncSession],
    path: Path,
    *,
    task: str,
    patch: str,
    description: str,
) -> uuid.UUID:
    digest = _sha(path)
    matches = dataset_match_ids(path)
    async with session_factory.begin() as session:
        existing = await session.scalar(
            select(DatasetManifestModel).where(DatasetManifestModel.sha256 == digest)
        )
        if existing is not None:
            return existing.id
        row = DatasetManifestModel(
            sha256=digest,
            task=task,
            patch=patch,
            description=description,
            match_list_known=matches is not None,
        )
        session.add(row)
        await session.flush()
        for match_id in sorted(matches or ()):
            session.add(DatasetManifestMatchModel(manifest_id=row.id, match_id=match_id))
        return row.id


async def register_version(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    task: str,
    scope: str,
    kind: str,
    name: str,
    artifact: Path,
    artifact_root: Path,
    dataset_manifest_id: uuid.UUID,
    feature_names: list[str],
    metrics: dict[str, Any],
) -> uuid.UUID:
    """MODEL needs a trusted artifact; BASELINE records the hash of its config file."""
    trusted = _trusted(artifact, artifact_root)
    async with session_factory.begin() as session:
        row = ModelVersionModel(
            task=task,
            scope=scope,
            kind=kind,
            name=name,
            artifact_path=str(trusted) if kind == "MODEL" else None,
            artifact_sha256=_sha(trusted),
            feature_names=feature_names,
            metrics=metrics,
            dataset_manifest_id=dataset_manifest_id,
            status="REGISTERED",
        )
        session.add(row)
        await session.flush()
        session.add(ModelEventModel(model_version_id=row.id, action="REGISTER", detail=name))
        return row.id


async def _lock(session: AsyncSession, task: str, scope: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"ml:{task}:{scope}"}
    )


async def promote(
    session_factory: async_sessionmaker[AsyncSession],
    version_id: uuid.UUID,
    *,
    action: str = "PROMOTE",
) -> None:
    async with session_factory.begin() as session:
        target = await session.get(ModelVersionModel, version_id)
        if target is None:
            raise RegistryError("versão desconhecida")
        await _lock(session, target.task, target.scope)
        await session.refresh(target)
        if target.kind == "MODEL":
            assert target.artifact_path is not None
            if _sha(Path(target.artifact_path)) != target.artifact_sha256:
                raise RegistryError("hash do artefato diverge do registrado; promoção recusada")
        dataset = await session.get(DatasetManifestModel, target.dataset_manifest_id)
        if dataset is not None and dataset.invalidated_at is not None:
            raise RegistryError(
                "dataset invalidado por expurgo; retreine e revalide antes de promover"
            )
        now = datetime.now(UTC)
        current = await session.scalar(
            select(ModelVersionModel).where(
                ModelVersionModel.task == target.task,
                ModelVersionModel.scope == target.scope,
                ModelVersionModel.status == "ACTIVE",
            )
        )
        if current is not None and current.id == target.id:
            return
        if current is not None:
            current.status, current.retired_at = "RETIRED", now
            session.add(ModelEventModel(model_version_id=current.id, action="RETIRE", detail=""))
            await session.flush()
        target.status, target.activated_at, target.retired_at = "ACTIVE", now, None
        session.add(ModelEventModel(model_version_id=target.id, action=action, detail=""))
    _LOADED.pop((target.task, target.scope), None)


async def rollback(
    session_factory: async_sessionmaker[AsyncSession], task: str, scope: str
) -> uuid.UUID:
    """Reactivate the version that was active before the current one."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(ModelEventModel.model_version_id, ModelVersionModel.status)
                .join(ModelVersionModel, ModelVersionModel.id == ModelEventModel.model_version_id)
                .where(
                    ModelVersionModel.task == task,
                    ModelVersionModel.scope == scope,
                    ModelEventModel.action.in_(("PROMOTE", "ROLLBACK")),
                )
                .order_by(ModelEventModel.created_at.desc(), ModelEventModel.id.desc())
            )
        ).all()
    previous = next((vid for vid, status in rows if status == "RETIRED"), None)
    if previous is None:
        raise RegistryError("não há versão anterior para rollback")
    await promote(session_factory, previous, action="ROLLBACK")
    return previous


async def resolve_active(
    session_factory: async_sessionmaker[AsyncSession], task: str, scope: str
) -> ActiveVersion:
    """Active version, loaded once per process and hash-checked; explicit fallback otherwise."""
    key = (task, scope)
    async with session_factory() as session:
        row = await session.scalar(
            select(ModelVersionModel).where(
                ModelVersionModel.task == task,
                ModelVersionModel.scope == scope,
                ModelVersionModel.status == "ACTIVE",
            )
        )
    if row is None:
        return ActiveVersion(None, "BASELINE", "B0", None, None, "NO_ACTIVE_VERSION")
    async with session_factory() as session:
        dataset = await session.get(DatasetManifestModel, row.dataset_manifest_id)
    if dataset is not None and dataset.invalidated_at is not None:
        _LOADED.pop(key, None)
        return ActiveVersion(None, "BASELINE", "B0", None, None, "ACTIVE_DATASET_INVALIDATED")
    cached = _LOADED.get(key)
    if cached is not None and cached.version_id == row.id:
        return cached
    if row.kind == "MODEL":
        assert row.artifact_path is not None
        if _sha(Path(row.artifact_path)) != row.artifact_sha256:
            return ActiveVersion(
                None, "BASELINE", "B0", None, None, "ACTIVE_ARTIFACT_HASH_MISMATCH"
            )
    loaded = ActiveVersion(row.id, row.kind, row.name, row.artifact_path, row.artifact_sha256)
    _LOADED[key] = loaded
    return loaded
