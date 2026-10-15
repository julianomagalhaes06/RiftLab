from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from sqlalchemy import distinct, func, select

from riftlab.catalog.infrastructure.catalog_mapping import load_catalog_mapping
from riftlab.catalog.models import ItemModel
from riftlab.catalog.patches import current_patch_window, persisted_mapping
from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.champions.application import champion_assets
from riftlab.champions.patterns import (
    MIN_PATTERN_SAMPLE,
    SAMPLE_LIMIT,
    ChampionChoice,
    ChampionPatterns,
    Observation,
    summarize_observations,
)
from riftlab.decisions.models import DecisionPointModel
from riftlab.decisions.scope import load_real_scope
from riftlab.decisions.selection import build_versions
from riftlab.ingestion.models import MatchModel, ParticipantModel
from riftlab.shared.api.matches import ChampionAsset
from riftlab.shared.db import read_only_unit_of_work
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
from riftlab.timeline.models import MatchReconstructionModel

router = APIRouter(tags=["champions"])


@router.get("/api/v1/champions/patterns", response_model=ChampionPatterns)
async def get_champion_patterns(
    request: Request,
    champion_id: Annotated[int | None, Query(alias="championId", ge=1)] = None,
    patch: Annotated[str | None, Query(pattern=r"^\d{1,3}\.\d{1,3}$")] = None,
    phase: Literal["first", "second", "sequence"] = "sequence",
    role: Literal["BOTTOM"] = "BOTTOM",
) -> ChampionPatterns:
    settings = request.app.state.settings
    async with read_only_unit_of_work(request.app.state.session_factory) as session:
        window = await current_patch_window(session)
        if window is None:
            return ChampionPatterns(
                patches=[],
                patch=patch,
                phase=phase,
                champions=[],
                champion_id=champion_id,
                status="EMPTY",
            )
        scope = load_real_scope(Path(settings.scope_path), window)
        patches = list(window[:2])
        chosen_patch = patch or patches[0]
        mapping = await persisted_mapping(
            session, load_catalog_mapping(Path(settings.catalog_mappings_path))
        )
        assets = await champion_assets(session, mapping, "BR1_1", chosen_patch, scope.champion_ids)
        base = (
            select(ParticipantModel.champion_id, func.count(distinct(ParticipantModel.match_id)))
            .join(MatchModel, MatchModel.match_id == ParticipantModel.match_id)
            .where(
                MatchModel.match_id.like("BR1_%"),
                MatchModel.patch == chosen_patch,
                MatchModel.queue == scope.queue,
                MatchModel.map_id == scope.map_id,
                ParticipantModel.role == role,
                ParticipantModel.champion_id.in_(scope.champion_ids),
            )
            .group_by(ParticipantModel.champion_id)
        )
        totals = {champion: count for champion, count in (await session.execute(base)).all()}
        choices = [
            ChampionChoice(
                champion_id=champion,
                champion=ChampionAsset(
                    name=assets[champion].name,
                    data_dragon_version=assets[champion].data_dragon_version,
                    image_file=assets[champion].image_file,
                )
                if champion in assets
                else None,
                matches=totals.get(champion, 0),
            )
            for champion in sorted(
                scope.champion_ids,
                key=lambda champion: assets[champion].name if champion in assets else str(champion),
            )
        ]
        chosen = (
            champion_id
            if champion_id is not None
            else (choices[0].champion_id if choices else None)
        )
        response = ChampionPatterns(
            patches=patches,
            patch=chosen_patch,
            phase=phase,
            champions=choices,
            champion_id=chosen,
            status="EMPTY",
        )
        if chosen not in scope.champion_ids or chosen_patch not in scope.patches:
            return response.model_copy(update={"status": "OUT_OF_SCOPE"})
        response.total_participations = totals.get(chosen, 0)
        participants = (
            await session.execute(
                select(ParticipantModel.match_id, ParticipantModel.participant_id)
                .join(MatchModel, MatchModel.match_id == ParticipantModel.match_id)
                .where(
                    MatchModel.match_id.like("BR1_%"),
                    MatchModel.patch == chosen_patch,
                    MatchModel.queue == scope.queue,
                    MatchModel.map_id == scope.map_id,
                    ParticipantModel.champion_id == chosen,
                    ParticipantModel.role == role,
                )
                .order_by(
                    MatchModel.start_at.desc(),
                    ParticipantModel.match_id,
                    ParticipantModel.participant_id,
                )
                .limit(SAMPLE_LIMIT)
            )
        ).all()
        response.sampled_participations = len(participants)
        if not participants:
            return response
        match_ids = [entry.match_id for entry in participants]
        points = (
            await session.scalars(
                select(DecisionPointModel)
                .join(
                    MatchReconstructionModel,
                    (MatchReconstructionModel.match_id == DecisionPointModel.match_id)
                    & (
                        MatchReconstructionModel.reconstruction_version
                        == DecisionPointModel.reconstruction_version
                    )
                    & (MatchReconstructionModel.catalog_id == DecisionPointModel.catalog_id),
                )
                .where(
                    DecisionPointModel.match_id.in_(match_ids),
                    DecisionPointModel.reconstruction_version == RECONSTRUCTION_ALGORITHM_VERSION,
                    DecisionPointModel.main_item_ordinal.in_([1, 2]),
                    DecisionPointModel.target_class == "MAIN",
                )
            )
        ).all()
        allowed = {(entry.match_id, entry.participant_id) for entry in participants}
        catalog_repo = SqlAlchemyCatalogRepository(session)
        observations = []
        catalogs = {}
        names = {}
        for point in points:
            if (point.match_id, point.participant_id) not in allowed or point.catalog_id is None:
                continue
            if point.catalog_id not in catalogs:
                catalogs[point.catalog_id] = await catalog_repo.get_catalog_version(
                    point.catalog_id
                )
                names[point.catalog_id] = {
                    item.item_id: item.attributes.get("name")
                    for item in (
                        await session.scalars(
                            select(ItemModel).where(ItemModel.catalog_id == point.catalog_id)
                        )
                    ).all()
                }
            catalog = catalogs[point.catalog_id]
            if catalog is None:
                continue
            versions = build_versions(scope, catalog)
            if any(
                getattr(point, field) != getattr(versions, field)
                for field in (
                    "selection_version",
                    "feature_schema_version",
                    "scope_version",
                    "classification_version",
                    "curation_rules_version",
                )
            ):
                continue
            observations.append(
                Observation(
                    point.match_id,
                    point.participant_id,
                    point.main_item_ordinal or 0,
                    point.target_item_id,
                    names[point.catalog_id].get(point.target_item_id),
                    catalog.data_dragon_version,
                    point.eligible,
                    point.id,
                    point.event_timestamp_ms,
                )
            )
        patterns, observed, eligible, denominator, situations = summarize_observations(
            observations, phase
        )
        response.observed_decisions = observed
        response.eligible_decisions = eligible
        response.pattern_denominator = denominator
        response.patterns = patterns
        response.situations = situations
        response.status = (
            "AVAILABLE"
            if denominator >= MIN_PATTERN_SAMPLE
            else ("SMALL_SAMPLE" if eligible else "NO_ELIGIBLE")
        )
        return response
