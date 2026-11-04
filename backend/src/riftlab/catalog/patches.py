"""Resolução auditável de patch e observação explícita do patch vigente.

A tabela `current_patch` guarda duas linhas independentes:
- `current`: a janela de análise, registrada só por `cc patch detect` (decide o recorte real);
- `published`: o último patch publicado no Data Dragon, isto é, o patch atual do jogo, renovado
  pelo worker no modo real. Serve para telas como Campeões e nunca move a janela de análise.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, String, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping, MappingEntry
from riftlab.catalog.models import CatalogVersionModel
from riftlab.shared.db import Base, read_only_unit_of_work, unit_of_work
from riftlab.shared.errors import SourceError, SourceSchemaError
from riftlab.shared.numeric import patch_order_key

logger = logging.getLogger(__name__)

PATCH_PATTERN = re.compile(r"^[0-9]{1,3}\.[0-9]{1,3}$")
VERSION_PATTERN = re.compile(r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$")
RESOLUTION_RULE = "first-published-v1"
CURRENT = "current"
PUBLISHED = "published"
# Falha ao ler versions.json: nova tentativa depois disso, sem insistir a cada ciclo do worker.
PUBLISHED_RETRY_SECONDS = 15 * 60


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


async def _upsert_patch_row(
    session: AsyncSession, singleton: str, versions_body: bytes, versions: tuple[str, ...]
) -> str:
    patch = versions[0].rsplit(".", 1)[0]
    adjacent = list(dict.fromkeys(version.rsplit(".", 1)[0] for version in versions))
    values = {
        "game_patch": patch,
        "detected_at": datetime.now(UTC),
        "versions_sha256": hashlib.sha256(versions_body).hexdigest(),
        "adjacent_patches": adjacent,
    }
    await session.execute(
        insert(CurrentPatchModel)
        .values(singleton=singleton, **values)
        .on_conflict_do_update(index_elements=["singleton"], set_=values)
    )
    return patch


async def record_current_patch(
    session_factory: async_sessionmaker[AsyncSession], versions_body: bytes
) -> str:
    """Janela de análise explícita (`cc patch detect`); também atualiza o patch publicado."""
    versions = parse_versions(versions_body)
    async with unit_of_work(session_factory) as session:
        patch = await _upsert_patch_row(session, CURRENT, versions_body, versions)
        await _upsert_patch_row(session, PUBLISHED, versions_body, versions)
    return patch


async def record_published_patch(
    session_factory: async_sessionmaker[AsyncSession], versions_body: bytes
) -> str:
    """Só o patch atual do jogo (linha `published`); a janela de análise não muda."""
    versions = parse_versions(versions_body)
    async with unit_of_work(session_factory) as session:
        return await _upsert_patch_row(session, PUBLISHED, versions_body, versions)


async def published_patch(session: AsyncSession) -> str | None:
    return await session.scalar(
        select(CurrentPatchModel.game_patch).where(CurrentPatchModel.singleton == PUBLISHED)
    )


class PublishedPatchRefresher:
    """Passo do worker: no modo real (chave da Riot configurada), lê o versions.json público
    a cada `refresh_seconds` e grava o patch atual do jogo. A demonstração nunca usa a rede.
    Falha mantém o valor anterior e só tenta de novo depois de PUBLISHED_RETRY_SECONDS."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Any,
        *,
        transport_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = session_factory
        self._settings = settings
        self._transport_factory = transport_factory
        self._clock = clock
        self._next_at = 0.0

    async def step(self) -> bool:
        """Sempre devolve False: não conta como trabalho de fila para o laço do worker."""
        if not self._settings.riot_api_key:
            return False
        now = self._clock()
        if now < self._next_at:
            return False
        if self._transport_factory is None:
            from riftlab.catalog.infrastructure.data_dragon_transport import DataDragonTransport

            transport = DataDragonTransport()
        else:
            transport = self._transport_factory()
        try:
            raw = await transport.fetch_versions()
            patch = await record_published_patch(self._factory, raw.body)
            self._next_at = now + self._settings.published_patch_refresh_seconds
            logger.info("patch publicado atualizado patch=%s", patch)
        except SourceError as exc:
            self._next_at = now + PUBLISHED_RETRY_SECONDS
            logger.warning("patch publicado indisponível code=%s", exc.code)
        finally:
            await transport.aclose()
        return False


async def current_patch(session: AsyncSession) -> str | None:
    return await session.scalar(
        select(CurrentPatchModel.game_patch).where(CurrentPatchModel.singleton == CURRENT)
    )


async def current_patch_window(session: AsyncSession) -> tuple[str, ...] | None:
    row = await session.get(CurrentPatchModel, CURRENT)
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
