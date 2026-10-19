"""CC-906: saúde das regras incremental, com o mesmo resultado da recontagem completa."""

from __future__ import annotations

import pytest
from pipeline_helpers import reset_match
from sqlalchemy import delete, select
from test_analyses import _decisions

from riftlab.ingestion.models import MatchModel, ParticipantFinalItemsModel
from riftlab.timeline import health
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
from riftlab.timeline.empirical_rules import EmpiricalRules
from riftlab.timeline.health import (
    MatchRuleHealthContributionModel as Contribution,
)
from riftlab.timeline.health import (
    PatchRuleHealthModel,
    compute_health,
    record_match_health,
)
from riftlab.timeline.models import MatchReconstructionModel

OTHER = "DEMO-0002"
PROVENANCE = [
    {"ruleId": "R1", "ruleVersion": 1, "application": "INHERITED", "appliedFromPatch": "16.19"}
]


def _normalized(reports: list[dict]) -> list[tuple]:
    return sorted(
        (
            r["queue"] or 0,
            r["application"],
            r["sourcePatch"],
            r["cleanParticipants"],
            r["failures"],
            r["contributingMatches"],
            r["verdict"],
        )
        for r in reports
    )


async def _set_provenance(session_factory, match_id: str, provenance: list[dict]) -> None:
    async with session_factory.begin() as session:
        marker = await session.get(
            MatchReconstructionModel, (match_id, RECONSTRUCTION_ALGORITHM_VERSION)
        )
        assert marker is not None
        marker.rules_provenance = provenance


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0001"], indirect=True)
async def test_incremental_health_matches_full_recount_and_refreshes_stale_matches(
    app_client, session_factory, tmp_path, _demo_match, monkeypatch
):
    await reset_match(session_factory, OTHER)
    try:
        for match_id in (_demo_match, OTHER):
            await _decisions(app_client, session_factory, tmp_path, match_id)
            await _set_provenance(session_factory, match_id, PROVENANCE)
        async with session_factory.begin() as session:
            patch = await session.scalar(
                select(MatchModel.patch).where(MatchModel.match_id == _demo_match)
            )
            other_hash = await session.scalar(
                select(MatchModel.raw_hash).where(MatchModel.match_id == OTHER)
            )
            # Só a DEMO-0001 tem inventário final oficial: copia para a outra partida.
            for row in (
                await session.scalars(
                    select(ParticipantFinalItemsModel).where(
                        ParticipantFinalItemsModel.match_id == _demo_match
                    )
                )
            ).all():
                session.add(
                    ParticipantFinalItemsModel(
                        match_id=OTHER,
                        participant_id=row.participant_id,
                        raw_hash=other_hash,
                        items=list(row.items),
                        trinket_item_id=row.trinket_item_id,
                        role_bound_item_id=row.role_bound_item_id,
                    )
                )
        assert patch is not None

        replays: list[int] = []
        original = health.reconstruct_participant

        def counting(**kwargs):
            replays.append(kwargs["participant_id"])
            return original(**kwargs)

        monkeypatch.setattr(health, "reconstruct_participant", counting)

        await record_match_health(session_factory, _demo_match)
        incremental = await record_match_health(session_factory, OTHER)
        full = await compute_health(session_factory, EmpiricalRules(), patch)
        assert _normalized(incremental) == _normalized(full)
        total = next(r for r in full if r["queue"] is None)
        assert total["cleanParticipants"] > 0

        # Contribuições em dia: o PREPARE de uma partida só refaz o replay dela.
        async with session_factory() as session:
            officials = len(
                (
                    await session.scalars(
                        select(ParticipantFinalItemsModel).where(
                            ParticipantFinalItemsModel.match_id == OTHER
                        )
                    )
                ).all()
            )
        replays.clear()
        await record_match_health(session_factory, OTHER)
        assert len(replays) == officials

        # Proveniência alterada em OUTRA partida: ela é recontada no próximo PREPARE.
        changed = [{**PROVENANCE[0], "appliedFromPatch": "16.18"}]
        await _set_provenance(session_factory, _demo_match, changed)
        replays.clear()
        after_change = await record_match_health(session_factory, OTHER)
        assert len(replays) > officials
        assert _normalized(after_change) == _normalized(
            await compute_health(session_factory, EmpiricalRules(), patch)
        )
        assert {r["sourcePatch"] for r in after_change} == {"16.18", "16.19"}

        # Inventário final oficial removido: a contagem da partida acompanha.
        async with session_factory.begin() as session:
            await session.execute(
                delete(ParticipantFinalItemsModel).where(
                    ParticipantFinalItemsModel.match_id == _demo_match
                )
            )
        refreshed = await record_match_health(session_factory, OTHER)
        assert _normalized(refreshed) == _normalized(
            await compute_health(session_factory, EmpiricalRules(), patch)
        )
        async with session_factory() as session:
            stored = await session.get(
                Contribution, (_demo_match, RECONSTRUCTION_ALGORITHM_VERSION)
            )
            assert stored is not None
            assert (stored.officials, stored.clean_participants) == (0, 0)

        # Partida apagada leva a contribuição junto (FK em cascata).
        await reset_match(session_factory, OTHER)
        async with session_factory() as session:
            assert (
                await session.get(Contribution, (OTHER, RECONSTRUCTION_ALGORITHM_VERSION)) is None
            )
    finally:
        await reset_match(session_factory, OTHER)
        async with session_factory.begin() as session:
            await session.execute(
                delete(PatchRuleHealthModel).where(PatchRuleHealthModel.game_patch.like("DEMO-%"))
            )
