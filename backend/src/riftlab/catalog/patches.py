"""Resolução auditável de patch e observação explícita do patch vigente."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping, MappingEntry
from riftlab.catalog.models import CatalogVersionModel
from riftlab.shared.db import Base, read_only_unit_of_work, unit_of_work
from riftlab.shared.errors import SourceSchemaError
from riftlab.shared.numeric import patch_order_key

PATCH_PATTERN = re.compile(r"^[0-9]{1,3}\.[0-9]{1,3}$")
VERSION_PATTERN = re.compile(r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$")
RESOLUTION_RULE = "first-published-v1"


class PatchResolutionModel(Base):
    __tablename__ = "patch_resolutions"

    game_patch: Mapped[str] = mapped_column(String, primary_key=True)
    data_dragon_version: Mapped[str] = mapped_column(String, nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    versions_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String, nullable=False)


class CurrentPatchModel(Base):
    __tablename__ = "current_patch"

    singleton: Mapped[str] = mapped_column(String, primary_key=True)
    game_patch: Mapped[str] = mapped_column(String, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    versions_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    adjacent_patches: Mapped[list[str]] = mapped_column(JSONB, nullable=False)


def parse_versions(body: bytes) -> tuple[str, ...]:
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceSchemaError(code="VERSIONS_INVALID", detail="versions.json inválido") from exc
    if (
        not isinstance(data, list)
        or not data
        or not all(isinstance(value, str) and value for value in data)
        or len(data) != len(set(data))
        or not VERSION_PATTERN.fullmatch(data[0])
    ):
        raise SourceSchemaError(code="VERSIONS_INVALID", detail="versions.json inválido")
    # A fonte oficial ainda inclui entradas históricas `lolpatch_*` e versões
    # com build de 7 dígitos. Elas não servem ao catálogo atual, mas não
    # invalidam a lista inteira.
    return tuple(value for value in data if VERSION_PATTERN.fullmatch(value))


def choose_version(patch: str, versions: tuple[str, ...]) -> str | None:
    """A última ocorrência na lista mais nova→mais antiga é a primeira publicada."""
    if not PATCH_PATTERN.fullmatch(patch):
        raise SourceSchemaError(code="PATCH_INVALID", detail="patch inválido")
    candidates = [version for version in versions if version.rsplit(".", 1)[0] == patch]
    return candidates[-1] if candidates else None


async def persisted_mapping(session: AsyncSession, overrides: CatalogMapping) -> CatalogMapping:
    rows = (await session.scalars(select(PatchResolutionModel))).all()
    entries = {row.game_patch: MappingEntry(row.data_dragon_version) for row in rows}
    entries.update(overrides.entries)  # override manual explícito tem precedência
    return CatalogMapping(overrides.schema_version, overrides.mapping_version, entries)


async def resolve_patch(
    session_factory: async_sessionmaker[AsyncSession],
    overrides: CatalogMapping,
    patch: str,
    versions_body: bytes,
) -> CatalogMapping:
    versions = parse_versions(versions_body)
    selected = choose_version(patch, versions)
    if selected is None:
        return overrides
    now = datetime.now(UTC)
    digest = hashlib.sha256(versions_body).hexdigest()
    async with unit_of_work(session_factory) as session:
        await session.execute(
            insert(PatchResolutionModel)
            .values(
                game_patch=patch,
                data_dragon_version=selected,
                resolved_at=now,
                versions_sha256=digest,
                rule_version=RESOLUTION_RULE,
            )
            .on_conflict_do_nothing(index_elements=["game_patch"])
        )
        return await persisted_mapping(session, overrides)


async def load_persisted_mapping(
    session_factory: async_sessionmaker[AsyncSession], overrides: CatalogMapping
) -> CatalogMapping:
    async with read_only_unit_of_work(session_factory) as session:
        return await persisted_mapping(session, overrides)


async def ensure_patch_mapping(
    session_factory: async_sessionmaker[AsyncSession],
    overrides: CatalogMapping,
    patch: str,
    transport: object,
) -> CatalogMapping:
    mapping = await load_persisted_mapping(session_factory, overrides)
    if mapping.resolve(patch) is not None:
        return mapping
    if not PATCH_PATTERN.fullmatch(patch):
        return mapping  # dado legado/sintético: sem URL dinâmica, catálogo ausente
    from riftlab.catalog.infrastructure.data_dragon_transport import DataDragonTransport

    assert isinstance(transport, DataDragonTransport)
    raw = await transport.fetch_versions()
    return await resolve_patch(session_factory, overrides, patch, raw.body)


async def record_current_patch(
    session_factory: async_sessionmaker[AsyncSession], versions_body: bytes
) -> str:
    versions = parse_versions(versions_body)
    patch = versions[0].rsplit(".", 1)[0]
    adjacent = list(dict.fromkeys(version.rsplit(".", 1)[0] for version in versions))
    digest = hashlib.sha256(versions_body).hexdigest()
    async with unit_of_work(session_factory) as session:
        await session.execute(
            insert(CurrentPatchModel)
            .values(
                singleton="current",
                game_patch=patch,
                detected_at=datetime.now(UTC),
                versions_sha256=digest,
                adjacent_patches=adjacent,
            )
            .on_conflict_do_update(
                index_elements=["singleton"],
                set_={
                    "game_patch": patch,
                    "detected_at": datetime.now(UTC),
                    "versions_sha256": digest,
                    "adjacent_patches": adjacent,
                },
            )
        )
    return patch


async def current_patch(session: AsyncSession) -> str | None:
    return await session.scalar(
        select(CurrentPatchModel.game_patch).where(CurrentPatchModel.singleton == "current")
    )


async def current_patch_window(session: AsyncSession) -> tuple[str, ...] | None:
    row = await session.get(CurrentPatchModel, "current")
    return tuple(row.adjacent_patches) if row else None


async def latest_catalog(session: AsyncSession) -> tuple[str, str] | None:
    """Patch e versão do Data Dragon do catálogo de patch real mais recente (faixa de contexto e
    ícones de invocador). Patches fora do formato do jogo (`16.20`; sintéticos) ficam de fora."""
    rows = (
        await session.execute(
            select(CatalogVersionModel.game_patch, CatalogVersionModel.data_dragon_version)
        )
    ).all()
    ranked = sorted(
        (
            (key, patch, version)
            for patch, version in rows
            if PATCH_PATTERN.match(patch) and (key := patch_order_key(patch)) is not None
        ),
        reverse=True,
    )
    return (ranked[0][1], ranked[0][2]) if ranked else None
