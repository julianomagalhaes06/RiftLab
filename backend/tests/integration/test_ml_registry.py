"""CC-405 model registry against a real PostgreSQL (migrations applied by the fixture)."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from riftlab.ml import registry
from riftlab.ml.models import ModelEventModel, ModelVersionModel
from riftlab.ml.registry import RegistryError


@pytest.fixture
def artifacts(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir()
    for name in ("b0.json", "m1.cbm", "m2.cbm"):
        (root / name).write_bytes(name.encode())
    return root


async def _setup(session_factory, artifacts: Path) -> tuple[str, uuid.UUID, uuid.UUID, uuid.UUID]:
    task = f"test-{uuid.uuid4().hex[:8]}"
    dataset = await registry.register_dataset(
        session_factory, artifacts / "b0.json", task=task, patch="16.19", description="sintético"
    )
    common = {
        "task": task,
        "scope": "S",
        "artifact_root": artifacts,
        "dataset_manifest_id": dataset,
    }
    b0 = await registry.register_version(
        session_factory,
        kind="BASELINE",
        name="B0",
        artifact=artifacts / "b0.json",
        feature_names=[],
        metrics={},
        **common,
    )
    m1 = await registry.register_version(
        session_factory,
        kind="MODEL",
        name="M1",
        artifact=artifacts / "m1.cbm",
        feature_names=["a"],
        metrics={"mrr": 0.75},
        **common,
    )
    m2 = await registry.register_version(
        session_factory,
        kind="MODEL",
        name="M2",
        artifact=artifacts / "m2.cbm",
        feature_names=["a"],
        metrics={"mrr": 0.76},
        **common,
    )
    return task, b0, m1, m2


@pytest.mark.asyncio
async def test_fallback_promotion_rollback_and_history(session_factory, artifacts: Path) -> None:
    task, b0, m1, _ = await _setup(session_factory, artifacts)

    fallback = await registry.resolve_active(session_factory, task, "S")
    assert (fallback.name, fallback.fallback_reason) == ("B0", "NO_ACTIVE_VERSION")

    await registry.promote(session_factory, b0)
    await registry.promote(session_factory, m1)
    active = await registry.resolve_active(session_factory, task, "S")
    assert (active.version_id, active.kind) == (m1, "MODEL")

    assert await registry.rollback(session_factory, task, "S") == b0
    active = await registry.resolve_active(session_factory, task, "S")
    assert active.version_id == b0
    async with session_factory() as session:
        retired = await session.get(ModelVersionModel, m1)
        events = (
            await session.scalars(
                select(ModelEventModel.action).where(ModelEventModel.model_version_id == m1)
            )
        ).all()
    assert retired is not None and retired.status == "RETIRED"  # never deleted
    assert sorted(events) == ["PROMOTE", "REGISTER", "RETIRE"]


@pytest.mark.asyncio
async def test_concurrent_promotions_leave_exactly_one_active(
    session_factory, artifacts: Path
) -> None:
    task, _, m1, m2 = await _setup(session_factory, artifacts)

    await asyncio.gather(
        registry.promote(session_factory, m1), registry.promote(session_factory, m2)
    )

    async with session_factory() as session:
        active = (
            await session.scalars(
                select(ModelVersionModel.id).where(
                    ModelVersionModel.task == task, ModelVersionModel.status == "ACTIVE"
                )
            )
        ).all()
    assert len(active) == 1


@pytest.mark.asyncio
async def test_changed_artifact_is_refused_and_falls_back(session_factory, artifacts: Path) -> None:
    task, _, m1, m2 = await _setup(session_factory, artifacts)
    await registry.promote(session_factory, m1)
    (artifacts / "m2.cbm").write_bytes(b"adulterado")

    with pytest.raises(RegistryError, match="hash"):
        await registry.promote(session_factory, m2)

    registry._LOADED.clear()
    (artifacts / "m1.cbm").write_bytes(b"adulterado")
    active = await registry.resolve_active(session_factory, task, "S")
    assert active.fallback_reason == "ACTIVE_ARTIFACT_HASH_MISMATCH" and active.name == "B0"


@pytest.mark.asyncio
async def test_untrusted_artifact_path_is_refused(
    session_factory, artifacts: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.cbm"
    outside.write_bytes(b"x")
    dataset = await registry.register_dataset(
        session_factory, artifacts / "b0.json", task="t", patch="16.19", description="s"
    )
    with pytest.raises(RegistryError, match="confiável"):
        await registry.register_version(
            session_factory,
            task="t",
            scope="S",
            kind="MODEL",
            name="X",
            artifact=outside,
            artifact_root=artifacts,
            dataset_manifest_id=dataset,
            feature_names=[],
            metrics={},
        )
