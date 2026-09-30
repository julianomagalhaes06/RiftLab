"""Extração de decisões (CC-204): orquestrador SÓ DE BANCO. Lê estados já
reconstruídos, avalia (puro) e persiste numa transação única, no mesmo padrão
de `reconstruct_match`: busca fora da transação de escrita -> avalia -> grava
marca + pontos juntos ou nada.

Fronteiras (por desenho, não por convenção):
- Nunca reconstrói, nunca abre fonte/raw e nunca acessa rede: a função não
  recebe fonte nem transporte. Sem marca de reconstrução na versão corrente,
  levanta RECONSTRUCTION_REQUIRED ("reconstruir antes").
- O catálogo é o `catalog_id` da MARCA de reconstrução (o realmente usado),
  nunca o canônico atual; a classificação usa o catálogo COMPLETO desse id e
  as exclusões persistidas, com a curadoria vinda da LINHA.
- Participantes são projetados para `ParticipantView` na borda: o
  `subject_hash` não atravessa esta camada.
- Recorte pela origem dos estados: REAL lê `configs/scope.yaml` (caminho de
  configuração), DEMO usa o recorte sintético explícito.
- Idempotente por marca de (partida, tupla de versões); versões novas geram
  linhas novas e as antigas ficam preservadas."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.classification import ItemClassificationTable, classify_catalog
from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.decisions.domain import (
    DecisionConfigError,
    DecisionError,
    DecisionExtractionMarker,
    DecisionInputError,
    DecisionVersions,
    MatchContext,
    ParticipantView,
    StoredDecisionPoint,
)
from riftlab.decisions.repository import SqlAlchemyDecisionRepository
from riftlab.decisions.scope import DecisionScope, load_real_scope, scope_for_origin
from riftlab.decisions.selection import DecisionPoint, build_versions, evaluate_decision
from riftlab.ingestion.domain import ImportMode, Match
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import ConflictError, NotFoundError
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
from riftlab.timeline.domain import DecisionState
from riftlab.timeline.repository import SqlAlchemyTimelineRepository

SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class DecisionExtraction:
    match_id: str
    marker: DecisionExtractionMarker
    reused: bool
    points: list[StoredDecisionPoint]

    def summary(self) -> dict[str, Any]:
        """Resumo sem dado pessoal: versões, contagens e motivos."""
        reasons: Counter[str] = Counter()
        sub_reasons: Counter[str] = Counter()
        for point in self.points:
            for reason in point.reasons:
                reasons[reason["code"]] += 1
                for sub in reason.get("subReasons", []):
                    sub_reasons[f"{reason['code']}:{sub}"] += 1
        return {
            "matchId": self.match_id,
            "reused": self.reused,
            "versions": self.marker.versions.to_payload(),
            "pointCount": self.marker.point_count,
            "eligibleCount": self.marker.eligible_count,
            "reasonCounts": dict(sorted(reasons.items())),
            "subReasonCounts": dict(sorted(sub_reasons.items())),
        }


def _origin_of(values: list[str]) -> SourceOrigin | None:
    """Origem a partir dos valores DISTINTOS de `quality.sourceOrigin` dos
    estados da versão corrente (vazio = nenhum estado)."""
    if not values:
        return None
    if len(set(values)) != 1:
        raise DecisionInputError(
            code="ORIGIN_INCONSISTENT", detail="estados da partida com origens diferentes"
        )
    try:
        return SourceOrigin(values[0])
    except ValueError as exc:
        raise DecisionInputError(
            code="ORIGIN_INVALID", detail="estado com sourceOrigin inválido"
        ) from exc


def _origin_from_import_mode(mode: ImportMode | None) -> SourceOrigin:
    if mode is None:
        raise DecisionError(
            code="ORIGIN_UNRESOLVED",
            detail="origem da partida indeterminada: sem estados e sem import concluído",
        )
    return SourceOrigin.CURATED_DEMO if mode is ImportMode.DEMO else SourceOrigin.COMPLETED_MATCH


async def _load_scope(
    session: AsyncSession, origin: SourceOrigin, scope_path: Path
) -> DecisionScope:
    if origin is SourceOrigin.CURATED_DEMO:
        return scope_for_origin(origin, None)
    if not scope_path.is_file():
        raise DecisionConfigError(
            code="SCOPE_FILE_MISSING",
            detail="recorte real (configs/scope.yaml) não encontrado no caminho configurado",
        )
    from riftlab.catalog.patches import current_patch_window

    window = await current_patch_window(session)
    return scope_for_origin(origin, load_real_scope(scope_path, window))


def _to_stored(point: DecisionPoint, extraction_id: uuid.UUID) -> StoredDecisionPoint:
    return StoredDecisionPoint(
        id=uuid.uuid4(),
        extraction_id=extraction_id,
        decision_state_id=point.state_id,
        match_id=point.match_id,
        participant_id=point.participant_id,
        event_source_index=point.event_source_index,
        event_timestamp_ms=point.event_timestamp_ms,
        versions=point.versions,
        target_item_id=point.target_item_id,
        target_class=point.target_class.value if point.target_class else None,
        main_item_ordinal=point.main_item_ordinal,
        eligible=point.eligible,
        reasons=[reason.to_payload() for reason in point.reasons],
        budget=point.budget.to_payload(),
        features=point.features.to_payload() if point.features else None,
    )


@dataclass(frozen=True, slots=True)
class CurrentVersions:
    """Contexto que define a tupla de versões CORRENTE de uma partida."""

    match: Match
    catalog: CatalogVersion | None
    origin: SourceOrigin
    scope: DecisionScope
    versions: DecisionVersions


async def resolve_current_versions(
    session: AsyncSession, match_id: str, scope_path: Path
) -> CurrentVersions:
    """ÚNICA definição de "versões correntes": reconstrução v6 (via a marca), o
    `catalog_id` DA MARCA (nunca o canônico atual), a curadoria da linha do
    catálogo, a classificação, a seleção, o schema de features e o recorte pela
    origem. Usada pela extração e pela leitura (API), sem duplicar a regra.
    Só lê o banco."""
    ingestion = SqlAlchemyIngestionRepository(session)
    timeline = SqlAlchemyTimelineRepository(session)
    catalog_repo = SqlAlchemyCatalogRepository(session)

    match = await ingestion.get_match(match_id)
    if match is None:
        raise NotFoundError(f"match {match_id!r} não encontrado; importar antes de extrair")
    reconstruction = await timeline.get_match_reconstruction(
        match_id, RECONSTRUCTION_ALGORITHM_VERSION
    )
    if reconstruction is None:
        raise DecisionError(
            code="RECONSTRUCTION_REQUIRED",
            detail=(
                f"partida sem reconstrução v{RECONSTRUCTION_ALGORITHM_VERSION}: "
                "reconstruir antes (cc timeline reconstruct); a extração nunca "
                "reconstrói nem usa estados de outra versão"
            ),
        )
    origin = _origin_of(
        await timeline.list_source_origins(match_id, RECONSTRUCTION_ALGORITHM_VERSION)
    ) or _origin_from_import_mode(await ingestion.get_match_import_mode(match_id))
    catalog: CatalogVersion | None = None
    if reconstruction.catalog_id is not None:  # o catálogo DA MARCA, não o canônico
        catalog = await catalog_repo.get_catalog_version(reconstruction.catalog_id)
        if catalog is None:
            raise DecisionInputError(
                code="CATALOG_NOT_FOUND", detail="catálogo da marca de reconstrução ausente"
            )
    scope = await _load_scope(session, origin, scope_path)
    return CurrentVersions(match, catalog, origin, scope, build_versions(scope, catalog))


async def extract_decisions(
    session_factory: SessionFactory, match_id: str, *, scope_path: Path
) -> DecisionExtraction:
    # --- leitura (fora de transação de escrita; nenhuma fonte, nenhuma rede) ---
    async with unit_of_work(session_factory) as session:
        ingestion = SqlAlchemyIngestionRepository(session)
        timeline = SqlAlchemyTimelineRepository(session)
        catalog_repo = SqlAlchemyCatalogRepository(session)

        current = await resolve_current_versions(session, match_id, scope_path)
        match, catalog, scope, versions = (
            current.match,
            current.catalog,
            current.scope,
            current.versions,
        )
        # Projeção na borda: o hash pessoal nunca sai desta linha.
        participants = tuple(
            ParticipantView.from_participant(participant)
            for participant in await ingestion.list_participants(match_id)
        )
        states: list[DecisionState] = []
        for participant in participants:
            states.extend(
                state
                for state in await timeline.list_decision_states(
                    match_id, participant.participant_id
                )
                if state.state_version == RECONSTRUCTION_ALGORITHM_VERSION
            )

        items: dict[int, Item] = {}
        excluded_ids: frozenset[int] = frozenset()
        classification: ItemClassificationTable | None = None
        if catalog is not None:
            items = {item.item_id: item for item in await catalog_repo.list_items(catalog.id)}
            excluded_ids = frozenset(
                excluded.item_id for excluded in await catalog_repo.list_excluded_items(catalog.id)
            )
            classification = classify_catalog(
                items,
                curation_rules_version=catalog.curation_rules_version,
                excluded_item_ids=excluded_ids,
            )

        decisions = SqlAlchemyDecisionRepository(session)
        existing = await decisions.get_extraction(match_id, versions)
        if existing is not None:
            return DecisionExtraction(
                match_id, existing, True, await decisions.list_points(existing.id)
            )

    # --- avaliação (pura) ---
    match_context = MatchContext.from_match(match)
    marker_id = uuid.uuid4()
    stored: list[StoredDecisionPoint] = []
    for state in sorted(states, key=lambda s: (s.participant_id, s.event_source_index)):
        evaluated = evaluate_decision(
            state,
            participants=participants,
            match=match_context,
            catalog=catalog,
            classification=classification,
            scope=scope,
        )
        if evaluated is not None:
            stored.append(_to_stored(evaluated, marker_id))
    marker = DecisionExtractionMarker(
        id=marker_id,
        match_id=match_id,
        versions=versions,
        point_count=len(stored),
        eligible_count=sum(1 for row in stored if row.eligible),
    )

    # --- persistência: marca + pontos na MESMA transação (tudo ou nada) ---
    try:
        async with unit_of_work(session_factory) as session:
            repo = SqlAlchemyDecisionRepository(session)
            await repo.add_extraction(marker)  # flush: extração concorrente -> ConflictError
            for row in stored:
                await repo.add_point(row)
    except IntegrityError as exc:
        raise ConflictError(
            f"extração concorrente de {match_id!r} já em andamento/concluída"
        ) from exc
    return DecisionExtraction(match_id, marker, False, stored)
