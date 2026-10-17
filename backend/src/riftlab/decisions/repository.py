from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Integer, Uuid, literal, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from riftlab.decisions.domain import (
    DecisionExtractionMarker,
    DecisionVersions,
    StoredDecisionPoint,
)
from riftlab.decisions.models import DecisionExtractionModel, DecisionPointModel
from riftlab.shared.errors import ConflictError
from riftlab.timeline.models import DecisionStateModel


class SqlAlchemyDecisionRepository:
    """Implementa DecisionRepository. Não faz commit: a UoW chamadora decide o
    limite da transação. Sem nenhuma operação de update: linhas imutáveis."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_extraction(
        self, match_id: str, versions: DecisionVersions
    ) -> DecisionExtractionMarker | None:
        stmt = select(DecisionExtractionModel).where(
            DecisionExtractionModel.match_id == match_id,
            DecisionExtractionModel.reconstruction_version == versions.reconstruction_version,
            DecisionExtractionModel.selection_version == versions.selection_version,
            DecisionExtractionModel.feature_schema_version == versions.feature_schema_version,
            DecisionExtractionModel.scope_version == versions.scope_version,
            # colunas anuláveis: IS NOT DISTINCT FROM (None casa com None)
            DecisionExtractionModel.classification_version.is_not_distinct_from(
                versions.classification_version
            ),
            DecisionExtractionModel.curation_rules_version.is_not_distinct_from(
                versions.curation_rules_version
            ),
            DecisionExtractionModel.catalog_id.is_not_distinct_from(versions.catalog_id),
        )
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        return _marker_from_row(row) if row is not None else None

    async def add_extraction(self, marker: DecisionExtractionMarker) -> None:
        self._session.add(
            DecisionExtractionModel(
                id=marker.id,
                match_id=marker.match_id,
                reconstruction_version=marker.versions.reconstruction_version,
                selection_version=marker.versions.selection_version,
                feature_schema_version=marker.versions.feature_schema_version,
                scope_version=marker.versions.scope_version,
                classification_version=marker.versions.classification_version,
                curation_rules_version=marker.versions.curation_rules_version,
                catalog_id=marker.versions.catalog_id,
                point_count=marker.point_count,
                eligible_count=marker.eligible_count,
            )
        )
        try:
            # flush imediato: a marca precisa existir antes dos pontos (FK) e a
            # violação de identidade da tupla aparece agora, traduzida para erro
            # de domínio (extração concorrente da mesma tupla).
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"já existe extração de decisões para match_id={marker.match_id!r} "
                "e esta tupla de versões"
            ) from exc

    async def add_point(self, point: StoredDecisionPoint) -> None:
        self._session.add(
            DecisionPointModel(
                id=point.id,
                extraction_id=point.extraction_id,
                decision_state_id=point.decision_state_id,
                match_id=point.match_id,
                participant_id=point.participant_id,
                event_source_index=point.event_source_index,
                event_timestamp_ms=point.event_timestamp_ms,
                reconstruction_version=point.versions.reconstruction_version,
                selection_version=point.versions.selection_version,
                feature_schema_version=point.versions.feature_schema_version,
                scope_version=point.versions.scope_version,
                classification_version=point.versions.classification_version,
                curation_rules_version=point.versions.curation_rules_version,
                catalog_id=point.versions.catalog_id,
                target_item_id=point.target_item_id,
                target_class=point.target_class,
                main_item_ordinal=point.main_item_ordinal,
                eligible=point.eligible,
                reasons=point.reasons,
                budget=point.budget,
                features=point.features,
            )
        )

    async def get_extraction_by_id(
        self, extraction_id: uuid.UUID
    ) -> DecisionExtractionMarker | None:
        row = await self._session.get(DecisionExtractionModel, extraction_id)
        return _marker_from_row(row) if row is not None else None

    async def list_points_page(
        self,
        extraction_id: uuid.UUID,
        participant_id: int,
        after: tuple[int, int, uuid.UUID] | None,
        limit: int,
    ) -> list[StoredDecisionPoint]:
        stmt = select(DecisionPointModel).where(
            DecisionPointModel.extraction_id == extraction_id,
            DecisionPointModel.participant_id == participant_id,
        )
        if after is not None:
            stmt = stmt.where(
                tuple_(
                    DecisionPointModel.event_timestamp_ms,
                    DecisionPointModel.event_source_index,
                    DecisionPointModel.id,
                )
                > tuple_(
                    literal(after[0], Integer),
                    literal(after[1], Integer),
                    literal(after[2], Uuid),
                )
            )
        stmt = stmt.order_by(
            DecisionPointModel.event_timestamp_ms,
            DecisionPointModel.event_source_index,
            DecisionPointModel.id,
        ).limit(limit)
        result = await self._session.execute(stmt)
        return [_point_from_row(row) for row in result.scalars()]

    async def get_point(self, point_id: uuid.UUID) -> StoredDecisionPoint | None:
        row = await self._session.get(DecisionPointModel, point_id)
        return _point_from_row(row) if row is not None else None

    async def get_pre_decision_context(
        self, decision_state_id: uuid.UUID
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        stmt = select(
            DecisionStateModel.snapshot["inventory"], DecisionStateModel.quality["levelObservation"]
        ).where(DecisionStateModel.id == decision_state_id)
        result = await self._session.execute(stmt)
        inventory, level = result.one()
        return inventory, level

    async def get_rules_provenance(self, decision_state_id: uuid.UUID) -> list[dict[str, Any]]:
        stmt = select(DecisionStateModel.quality["empiricalRules"]).where(
            DecisionStateModel.id == decision_state_id
        )
        value = (await self._session.execute(stmt)).scalar_one()
        return [rule for rule in value or [] if isinstance(rule, dict)]

    async def list_extractions(self, match_id: str) -> list[DecisionExtractionMarker]:
        stmt = (
            select(DecisionExtractionModel)
            .where(DecisionExtractionModel.match_id == match_id)
            .order_by(DecisionExtractionModel.created_at, DecisionExtractionModel.id)
        )
        result = await self._session.execute(stmt)
        return [_marker_from_row(row) for row in result.scalars()]

    async def list_points(self, extraction_id: uuid.UUID) -> list[StoredDecisionPoint]:
        stmt = (
            select(DecisionPointModel)
            .where(DecisionPointModel.extraction_id == extraction_id)
            .order_by(
                DecisionPointModel.event_timestamp_ms,
                DecisionPointModel.event_source_index,
                DecisionPointModel.id,
            )
        )
        result = await self._session.execute(stmt)
        return [_point_from_row(row) for row in result.scalars()]


def _versions_from_row(row: DecisionExtractionModel | DecisionPointModel) -> DecisionVersions:
    return DecisionVersions(
        reconstruction_version=row.reconstruction_version,
        selection_version=row.selection_version,
        feature_schema_version=row.feature_schema_version,
        scope_version=row.scope_version,
        catalog_id=row.catalog_id,
        curation_rules_version=row.curation_rules_version,
        classification_version=row.classification_version,
    )


def _marker_from_row(row: DecisionExtractionModel) -> DecisionExtractionMarker:
    return DecisionExtractionMarker(
        id=row.id,
        match_id=row.match_id,
        versions=_versions_from_row(row),
        point_count=row.point_count,
        eligible_count=row.eligible_count,
    )


def _point_from_row(row: DecisionPointModel) -> StoredDecisionPoint:
    return StoredDecisionPoint(
        id=row.id,
        extraction_id=row.extraction_id,
        decision_state_id=row.decision_state_id,
        match_id=row.match_id,
        participant_id=row.participant_id,
        event_source_index=row.event_source_index,
        event_timestamp_ms=row.event_timestamp_ms,
        versions=_versions_from_row(row),
        target_item_id=row.target_item_id,
        target_class=row.target_class,
        main_item_ordinal=row.main_item_ordinal,
        eligible=row.eligible,
        reasons=row.reasons,
        budget=row.budget,
        features=row.features,
    )
