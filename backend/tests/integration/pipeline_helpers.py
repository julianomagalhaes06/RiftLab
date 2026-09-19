"""Helpers de integração compartilhados (CC-203/CC-204): partida demo
genuinamente persistida pelo importador real (CC-201), catálogo demo real
(CC-202) e limpeza da árvore completa de uma partida, na ordem que respeita as
FKs. Sem lógica de teste aqui."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete

from riftlab.catalog.application import sync_catalog
from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping
from riftlab.catalog.infrastructure.demo_source import DemoCatalogSource, demo_catalog_mapping
from riftlab.config import Settings
from riftlab.decisions.models import DecisionExtractionModel, DecisionPointModel
from riftlab.ingestion.application import process_next_job
from riftlab.ingestion.domain import ImportJob, ImportJobState, ImportMode
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import ImportJobModel, MatchModel, ParticipantModel
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work
from riftlab.timeline.models import DecisionStateModel, MatchReconstructionModel, TimelineEventModel

FAKE_SUBJECT_HASH_SECRET = "segredo-local-de-teste-nao-real"  # noqa: S105

# DEMO-1 é o gamePatch real usado pelas fixtures demo de partida (CC-104) e
# pelo catálogo demo real (CC-202, fixtures/catalogs/demo_1.json) — os dois
# já reconciliados: item 100010 = 2x100001 + 1x100002, exatamente o cenário
# de demo_0002_receita_componentes.json. Derivado da própria fixture (mesmo
# helper usado pela CLI), nunca um literal solto duplicado aqui.
DEMO_1_MAPPING: CatalogMapping = demo_catalog_mapping()


async def reset_match(session_factory, match_id: str) -> None:
    """Achado real de higiene (mesma lição de test_worker_processing.py,
    CC-201): DEMO-0001/0002/0003 são identidades fixas COMPARTILHADAS com
    test_import_routes.py/test_worker_processing.py neste mesmo processo
    pytest. Uma partida deixada SUCCEEDED bloqueia permanentemente a criação
    de job desses arquivos-irmãos (índice único de import_jobs cobre SUCCEEDED)
    — limpa a árvore completa da partida (pontos e extrações de decisão, marca
    de reconstrução, decisões, eventos, participantes, match, job) na ordem
    que respeita as FKs. DELETE é permitido nas tabelas imutáveis; só UPDATE é
    rejeitado."""
    async with unit_of_work(session_factory) as session:
        await session.execute(
            delete(DecisionPointModel).where(DecisionPointModel.match_id == match_id)
        )
        await session.execute(
            delete(DecisionExtractionModel).where(DecisionExtractionModel.match_id == match_id)
        )
        await session.execute(
            delete(MatchReconstructionModel).where(MatchReconstructionModel.match_id == match_id)
        )
        await session.execute(
            delete(DecisionStateModel).where(DecisionStateModel.match_id == match_id)
        )
        await session.execute(
            delete(TimelineEventModel).where(TimelineEventModel.match_id == match_id)
        )
        await session.execute(
            delete(ImportJobModel).where(ImportJobModel.requested_match_id == match_id)
        )
        await session.execute(delete(ParticipantModel).where(ParticipantModel.match_id == match_id))
        await session.execute(delete(MatchModel).where(MatchModel.match_id == match_id))


async def sync_demo_catalog(session_factory) -> uuid.UUID:
    version = await sync_catalog(session_factory, DemoCatalogSource(), "DEMO-1")
    return version.id


async def import_demo_match(session_factory, tmp_path, requested_match_id: str) -> None:
    async with unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        job = ImportJob(
            id=uuid.uuid4(),
            requested_match_id=requested_match_id,
            mode=ImportMode.DEMO,
            state=ImportJobState.QUEUED,
        )
        await repo.create_import_job(job)

    settings = Settings(subject_hash_secret=FAKE_SUBJECT_HASH_SECRET)
    raw_store = DurableRawMatchStore(tmp_path / "raw")
    processed = await process_next_job(session_factory, raw_store, settings)
    assert processed is True


def config_path(name: str) -> Path:
    """configs/<name> no container (/app/configs, copiado pelo Dockerfile) ou na
    raiz do repositório (host). Ausência nos dois é falha, não skip."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] / "configs" / name, here.parents[3] / "configs" / name):
        if candidate.is_file():
            return candidate
    pytest.fail(f"configs/{name} não encontrado nem no container nem no repositório")
