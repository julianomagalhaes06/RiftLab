"""Serviço de aplicação de catálogo (CC-202): busca (rede fora de transação)
-> valida (pura, em memória) -> persiste catálogo+itens numa transação
única. Sync idempotente sob concorrência real: identidade de conteúdo
(CatalogVersion.sha256) é o índice único que resolve a corrida — a chamada
perdedora nunca duplica, reconsulta e reaproveita a linha vencedora (mesmo
padrão de ingestion.request_import, CC-201)."""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.domain import (
    CatalogSource,
    CatalogSourceResult,
    CatalogVersion,
    Item,
    validate_item_types_and_limits,
    validate_recipe_graph,
)
from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping
from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import ConflictError

SessionFactory = async_sessionmaker[AsyncSession]


def compute_catalog_content_hash(result: CatalogSourceResult) -> str:
    """Identidade de conteúdo (CC-202, plano: "hashes/identidade devem
    incluir alterações da curadoria e mapping, além do raw, para não
    reutilizar conteúdo derivado desatualizado"). raw_hash já é o hash dos
    bytes brutos lidos pela fonte; curadoria é função pura desses bytes mais
    curation_rules_version, então não é preciso re-hashear cada item
    individualmente — mudar qualquer um dos quatro campos abaixo muda a
    identidade."""
    canonical = json.dumps(
        {
            "gamePatch": result.game_patch,
            "dataDragonVersion": result.data_dragon_version,
            "mappingVersion": result.mapping_version,
            "curationRulesVersion": result.curation_rules_version,
            "rawHash": result.raw_hash,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def sync_catalog(
    session_factory: SessionFactory, source: CatalogSource, game_patch: str
) -> CatalogVersion:
    """MISSING_CATALOG (patch fora do mapping/manifesto) propaga como
    SourceNotFoundError/SourceSchemaError da própria fonte — nunca captado
    aqui, nunca substituído por fallback algum. Todo sync bem-sucedido
    (idempotente ou não) termina marcando sua identidade de conteúdo como
    canônica para (game_patch, data_dragon_version, mapping_version) — ação
    deliberada (revisão CC-202), nunca inferida por ordem de inserção."""
    result = await source.load_catalog(game_patch)
    validate_item_types_and_limits(result.items)
    validate_recipe_graph(result.items)

    content_hash = compute_catalog_content_hash(result)

    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyCatalogRepository(session)
        existing = await repo.find_catalog_version_by_content_hash(content_hash)
        if existing is not None:
            # Idempotente: mesmo conteúdo já sincronizado antes — ainda
            # assim confirma a associação canônica (auto-recuperação se por
            # algum motivo esta linha não fosse mais a canônica).
            await repo.mark_canonical(existing.id)
    if existing is not None:
        return existing

    new_catalog = CatalogVersion(
        id=uuid.uuid4(),
        game_patch=result.game_patch,
        data_dragon_version=result.data_dragon_version,
        sha256=content_hash,
        mapping_version=result.mapping_version,
        raw_hash=result.raw_hash,
        curation_rules_version=result.curation_rules_version,
    )
    try:
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyCatalogRepository(session)
            await repo.add_catalog_version(new_catalog)
            for item in result.items:
                await repo.add_item(
                    Item(
                        catalog_id=new_catalog.id,
                        item_id=item.item_id,
                        price=item.price,
                        recipe=item.recipe,
                        attributes=item.attributes,
                        flags=item.flags,
                        schema_version=item.schema_version,
                    )
                )
            for excluded in result.excluded_items:
                await repo.add_excluded_item(new_catalog.id, excluded)
            await repo.mark_canonical(new_catalog.id)
        return new_catalog
    except ConflictError:
        # Corrida real: outro sync com o MESMO conteúdo publicou primeiro.
        # A linha perdedora nunca fica parcialmente gravada (transação
        # inteira revertida) e nunca sobrescreve a vencedora — reconsulta,
        # confirma a associação canônica e devolve a mesma identidade.
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyCatalogRepository(session)
            winner = await repo.find_catalog_version_by_content_hash(content_hash)
            if winner is None:  # pragma: no cover - defensivo, não deveria acontecer
                raise
            await repo.mark_canonical(winner.id)
        return winner


async def resolve_catalog_for_match(
    session_factory: SessionFactory, mapping: CatalogMapping, game_patch: str
) -> CatalogVersion | None:
    """None = MISSING_CATALOG: patch sem entrada no mapping explícito (nunca
    tenta rede para adivinhar), OU coberto pelo mapping mas sem catálogo
    canônico definido (nunca sincronizado, ou legado ambíguo). Resolução
    determinística: a identidade (game_patch, data_dragon_version,
    mapping_version) é uma linha própria (catalog_identities), nunca
    ORDER BY created_at sobre catalog_versions (empate possível, escolha
    incidental por data)."""
    entry = mapping.resolve(game_patch)
    if entry is None:
        return None
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyCatalogRepository(session)
        return await repo.find_canonical_catalog_version(
            game_patch, entry.data_dragon_version, mapping.mapping_version
        )
