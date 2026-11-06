"""Helpers de integração compartilhados (CC-203/CC-204): partida demo
genuinamente persistida pelo importador real (CC-201), catálogo demo real
(CC-202) e limpeza da árvore completa de uma partida, na ordem que respeita as
FKs. Sem lógica de teste aqui."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select

from riftlab.analyses.models import AnalysisModel
from riftlab.catalog.application import sync_catalog
from riftlab.catalog.domain import CatalogItemSourceDTO, CatalogSourceResult
from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping
from riftlab.catalog.infrastructure.curation import CURATION_RULES_VERSION
from riftlab.catalog.infrastructure.demo_source import DemoCatalogSource, demo_catalog_mapping
from riftlab.config import Settings
from riftlab.decisions.models import DecisionExtractionModel, DecisionPointModel
from riftlab.ingestion.application import process_next_job
from riftlab.ingestion.domain import ImportJob, ImportJobState, ImportMode
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import (
    ImportJobModel,
    MatchModel,
    ParticipantFinalItemsModel,
    ParticipantModel,
    RiotRateLimitModel,
)
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.learning.models import LearningAttemptModel
from riftlab.reviews.models import AnalysisFeedbackModel
from riftlab.shared.db import unit_of_work
from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.models import (
    DecisionStateModel,
    MatchFramesModel,
    MatchReconstructionModel,
    TimelineEventModel,
)

FAKE_SUBJECT_HASH_SECRET = "segredo-local-de-teste-nao-real"  # noqa: S105
FAKE_RIOT_API_KEY = "RGAPI-fake-test-key-not-real"  # noqa: S105

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
            delete(LearningAttemptModel).where(
                LearningAttemptModel.decision_id.in_(
                    select(DecisionPointModel.id).where(DecisionPointModel.match_id == match_id)
                )
            )
        )
        await session.execute(
            delete(AnalysisFeedbackModel).where(
                AnalysisFeedbackModel.analysis_id.in_(
                    select(AnalysisModel.id).where(
                        AnalysisModel.decision_id.in_(
                            select(DecisionPointModel.id).where(
                                DecisionPointModel.match_id == match_id
                            )
                        )
                    )
                )
            )
        )
        await session.execute(
            delete(AnalysisModel).where(
                AnalysisModel.decision_id.in_(
                    select(DecisionPointModel.id).where(DecisionPointModel.match_id == match_id)
                )
            )
        )
        await session.execute(
            delete(DecisionPointModel).where(DecisionPointModel.match_id == match_id)
        )
        await session.execute(
            delete(DecisionExtractionModel).where(DecisionExtractionModel.match_id == match_id)
        )
        await session.execute(
            delete(MatchReconstructionModel).where(MatchReconstructionModel.match_id == match_id)
        )
        await session.execute(delete(MatchFramesModel).where(MatchFramesModel.match_id == match_id))
        await session.execute(
            delete(DecisionStateModel).where(DecisionStateModel.match_id == match_id)
        )
        await session.execute(
            delete(TimelineEventModel).where(TimelineEventModel.match_id == match_id)
        )
        await session.execute(
            delete(ImportJobModel).where(ImportJobModel.requested_match_id == match_id)
        )
        await session.execute(
            delete(ParticipantFinalItemsModel).where(
                ParticipantFinalItemsModel.match_id == match_id
            )
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


async def purge_open_preparations(session_factory) -> None:
    """Preparações ainda abertas de outros testes (o import de qualquer partida
    enfileira uma) nunca podem ser reservadas por `process_next_preparation` de
    um teste que não é delas."""
    async with unit_of_work(session_factory) as session:
        await session.execute(
            delete(ImportJobModel).where(
                ImportJobModel.kind == "PREPARE",
                ImportJobModel.state.in_(["QUEUED", "RUNNING", "RETRY_WAIT"]),
            )
        )


def unique_patch() -> str:
    """Patch sintético único por teste (major.minor): nenhum catálogo canônico
    deixado por outra execução pode mascarar o caminho de sincronização."""
    return f"{uuid.uuid4().int % 10**6 + 100}.{uuid.uuid4().int % 90 + 10}"


def real_match_docs(
    match_id: str, patch: str, purchases: tuple[tuple[int, int], ...] = ()
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Match-V5/Timeline-V5 mínimos e válidos (participante 1 = UTILITY/89).
    `purchases` = (timestamp_ms, itemId) do participante 1."""
    participants = [
        {
            "participantId": 1,
            "puuid": "puuid-1",
            "championId": 89,
            "teamId": 100,
            "teamPosition": "UTILITY",
        },
        {
            "participantId": 2,
            "puuid": "puuid-2",
            "championId": 51,
            "teamId": 200,
            "teamPosition": "BOTTOM",
        },
    ]
    match_doc = {
        "metadata": {"matchId": match_id, "participants": ["puuid-1", "puuid-2"]},
        "info": {
            "queueId": 420,
            "mapId": 11,
            "gameVersion": f"{patch}.123.4567",
            "gameStartTimestamp": 1_700_000_000_000,
            "gameDuration": 1800,
            "endOfGameResult": "GameComplete",
            "participants": participants,
        },
    }
    events = [
        {"timestamp": at, "type": "ITEM_PURCHASED", "participantId": 1, "itemId": item_id}
        for at, item_id in purchases
    ]
    frames: list[dict[str, Any]] = [
        {"timestamp": 0, "participantFrames": {}, "events": []},
        {
            "timestamp": 60_000,
            "participantFrames": {"1": {"currentGold": 500, "level": 3}},
            "events": events,
        },
    ]
    timeline_doc = {
        "metadata": {"matchId": match_id},
        "info": {
            "frameInterval": 60000,
            "participants": [
                {"participantId": 1, "puuid": "puuid-1"},
                {"participantId": 2, "puuid": "puuid-2"},
            ],
            "frames": frames,
        },
    }
    return match_doc, timeline_doc


async def import_real_match(
    session_factory,
    raw_store: DurableRawMatchStore,
    match_id: str,
    patch: str,
    purchases: tuple[tuple[int, int], ...] = (),
    *,
    prepare_after: bool = True,
) -> None:
    """Importa uma partida REAL com transporte HTTP simulado (nunca rede real):
    o raw fica no `raw_store` local, e a preparação depois só lê esse raw.
    `prepare_after=False` imita a sincronização de histórico (sem preparação agendada)."""
    async with unit_of_work(session_factory) as session:
        await session.execute(delete(RiotRateLimitModel))
    async with unit_of_work(session_factory) as session:
        await SqlAlchemyIngestionRepository(session).create_import_job(
            ImportJob(
                id=uuid.uuid4(),
                requested_match_id=match_id,
                mode=ImportMode.RIOT,
                state=ImportJobState.QUEUED,
                prepare_after=prepare_after,
            )
        )
    match_doc, timeline_doc = real_match_docs(match_id, patch, purchases)

    def handler(request: httpx.Request) -> httpx.Response:
        doc = timeline_doc if request.url.path.endswith("/timeline") else match_doc
        return httpx.Response(200, content=json.dumps(doc).encode())

    settings = Settings(
        subject_hash_secret=FAKE_SUBJECT_HASH_SECRET, riot_api_key=FAKE_RIOT_API_KEY
    )
    processed = await process_next_job(
        session_factory,
        raw_store,
        settings,
        riot_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert processed is True


def write_prepare_configs(
    directory: Path,
    *,
    scope_patch: str,
    mapped_patches: tuple[str, ...],
    queues: tuple[int, ...] = (420,),
) -> Settings:
    """Mapping e recorte próprios do teste (patch sintético). Sem `mapped_patches`
    contendo o patch da partida, a preparação REAL não pode tocar a rede."""
    entries = "".join(
        f'  "{patch}":\n    dataDragonVersion: "{patch}.1"\n' for patch in mapped_patches
    )
    mapping = directory / "catalog-mappings.yaml"
    mapping.write_text(f"schemaVersion: v1\nmappingVersion: v1\nmappings:\n{entries}")
    scope = directory / "scope.yaml"
    queue_yaml = f"queues: {list(queues)}\n" if len(queues) > 1 else f"queue: {queues[0]}\n"
    scope.write_text(
        "schemaVersion: v3\nfrozenAt: '2026-09-19'\n"
        f"{queue_yaml}map: 11\nrole: UTILITY\n"
        f"championIds: [89]\npatches:\n  - gamePatch: '{scope_patch}'\n"
    )
    return Settings(
        subject_hash_secret=FAKE_SUBJECT_HASH_SECRET,
        catalog_mappings_path=str(mapping),
        scope_path=str(scope),
    )


class StubCatalogSource:
    """Fonte de catálogo sem I/O para o modo REAL (nunca rede real); conta as
    chamadas. `with_names=False` devolve itens sem `attributes.name`."""

    def __init__(
        self, *, with_names: bool = True, curation_rules_version: str = CURATION_RULES_VERSION
    ) -> None:
        self.loads: list[str] = []
        self._with_names = with_names
        self._curation = curation_rules_version

    async def load_catalog(self, game_patch: str) -> CatalogSourceResult:
        self.loads.append(game_patch)
        attributes = {"name": "Botas Sintéticas"} if self._with_names else {}
        return CatalogSourceResult(
            game_patch=game_patch,
            origin=SourceOrigin.COMPLETED_MATCH,
            schema_version="test_v1",
            raw_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            data_dragon_version=f"{game_patch}.1",
            mapping_version="v1",
            # a mesma curadoria que a fonte REAL emite: com outra, a preparação (que só aceita
            # o canônico com a curadoria corrente) sincronizaria de novo a cada partida
            curation_rules_version=self._curation,
            items=(
                CatalogItemSourceDTO(
                    item_id=3006, price=1100, attributes=attributes, flags={"completed": True}
                ),
            ),
        )


def stub_catalog_factory(stub: StubCatalogSource | None = None, error: Exception | None = None):
    """`catalog_source_factory` injetável em `process_next_preparation`;
    `factory.opened` conta quantas vezes a fonte foi aberta (rede = 0)."""

    @asynccontextmanager
    async def factory(mapping: CatalogMapping) -> AsyncIterator[Any]:
        factory.opened += 1  # type: ignore[attr-defined]
        if error is not None:
            raise error
        assert stub is not None
        yield stub

    factory.opened = 0  # type: ignore[attr-defined]
    return factory
