"""Fatos finais imutáveis e suspensão dinâmica sem reescrever decisões."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pipeline_helpers import import_demo_match
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from test_analyses import _decisions

from riftlab.decisions.models import DecisionPointModel
from riftlab.ingestion.domain import ImportMode
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import MatchModel, ParticipantFinalItemsModel
from riftlab.ingestion.purge import purge_match
from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION
from riftlab.timeline.domain import TimelineEvent
from riftlab.timeline.empirical_rules import EmpiricalRules
from riftlab.timeline.health import (
    PatchRuleHealthModel,
    compute_health,
    fingerprint,
    health_for_state,
)
from riftlab.timeline.models import DecisionStateModel, MatchReconstructionModel
from riftlab.timeline.repository import SqlAlchemyTimelineRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0001"], indirect=True)
async def test_official_items_imported_read_and_immutable(
    app_client, session_factory, tmp_path, _demo_match
):
    await import_demo_match(session_factory, tmp_path, _demo_match)
    detail = (await app_client.get("/api/v1/matches/" + _demo_match)).json()
    assert detail["participants"][0]["finalItems"] == [100001]
    assert detail["participants"][1]["finalItems"] == []
    async with session_factory() as session:
        assert (
            len(
                (
                    await session.scalars(
                        select(ParticipantFinalItemsModel).where(
                            ParticipantFinalItemsModel.match_id == _demo_match
                        )
                    )
                ).all()
            )
            == 2
        )
        with pytest.raises(DBAPIError):
            await session.execute(
                text("UPDATE participant_final_items SET items='[]' WHERE match_id=:m"),
                {"m": _demo_match},
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_changing_post_match_facts_does_not_change_decision_or_analysis(
    app_client, session_factory, tmp_path, _demo_match
):
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    decision_id = decision["decisionId"]
    first_analysis = await app_client.post(
        "/api/v1/analyses",
        json={"decisionId": decision_id, "objective": {"priority": "BALANCED"}},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert first_analysis.status_code == 201
    async with session_factory.begin() as session:
        match = await session.get(MatchModel, _demo_match)
        points = (
            await session.scalars(
                select(DecisionPointModel).where(DecisionPointModel.match_id == _demo_match)
            )
        ).all()
        state_ids = {point.decision_state_id for point in points}
        before_states = {
            state.id: (state.snapshot, state.features, state.quality)
            for state in (
                await session.scalars(
                    select(DecisionStateModel).where(DecisionStateModel.id.in_(state_ids))
                )
            ).all()
        }
        await session.execute(
            delete(ParticipantFinalItemsModel).where(
                ParticipantFinalItemsModel.match_id == _demo_match
            )
        )
        for participant_id in (1, 6):
            session.add(
                ParticipantFinalItemsModel(
                    match_id=_demo_match,
                    participant_id=participant_id,
                    items=[999999],
                    trinket_item_id=999998,
                    role_bound_item_id=999997,
                    raw_hash=match.raw_hash,
                )
            )
    second_analysis = await app_client.post(
        "/api/v1/analyses",
        json={"decisionId": decision_id, "objective": {"priority": "BALANCED"}},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert second_analysis.status_code == 201
    assert [c["itemId"] for c in first_analysis.json()["candidates"]] == [
        c["itemId"] for c in second_analysis.json()["candidates"]
    ]
    detail = (await app_client.get("/api/v1/decisions/" + decision_id)).json()
    assert "finalItems" not in detail
    assert "finalTrinketItemId" not in detail
    assert "roleBoundItemId" not in detail
    async with session_factory() as session:
        after_states = {
            state.id: (state.snapshot, state.features, state.quality)
            for state in (
                await session.scalars(
                    select(DecisionStateModel).where(DecisionStateModel.id.in_(state_ids))
                )
            ).all()
        }
    assert after_states == before_states
    match_detail = (await app_client.get("/api/v1/matches/" + _demo_match)).json()
    assert match_detail["participants"][0]["finalItems"] == [999999]
    assert match_detail["participants"][0]["finalTrinketItemId"] == 999998


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0001"], indirect=True)
async def test_compute_health_replays_persisted_events_with_marker_parameters(
    app_client, session_factory, tmp_path, _demo_match
):
    await _decisions(app_client, session_factory, tmp_path, _demo_match)
    provenance = [
        {
            "ruleId": "R4s",
            "ruleVersion": 1,
            "application": "INHERITED",
            "appliedFromPatch": "16.19",
            "systemItemIds": [7654321],
        }
    ]
    async with session_factory.begin() as session:
        marker = await session.get(
            MatchReconstructionModel, (_demo_match, RECONSTRUCTION_ALGORITHM_VERSION)
        )
        assert marker is not None and marker.catalog_id is not None
        marker.rules_provenance = provenance
        repo = SqlAlchemyTimelineRepository(session)
        events = await repo.list_events(_demo_match)
        await repo.add_event(
            TimelineEvent(
                match_id=_demo_match,
                source_index=max(event.source_index for event in events) + 1,
                timestamp_ms=55000,
                type="ITEM_DESTROYED",
                payload={"participantId": 1, "itemId": 7654321},
            )
        )
    # A simula configuração corrente divergente. O replay deve honrar o parâmetro
    # A do marcador (sem incerteza para esse destroy), nunca B.
    reports = await compute_health(
        session_factory,
        EmpiricalRules(system_item_ids=frozenset({9876543})),
        "DEMO-1",
    )
    total = next(report for report in reports if report["queue"] is None)
    assert total["cleanParticipants"] == 2
    assert total["contributingMatches"] == 1
    assert total["failures"] == 0
    async with session_factory() as session:
        row = await session.scalar(
            select(PatchRuleHealthModel).where(
                PatchRuleHealthModel.game_patch == "DEMO-1",
                PatchRuleHealthModel.queue.is_(None),
            )
        )
        assert row is not None and row.rules_fingerprint == fingerprint(provenance)


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_purging_last_accept_contributor_invalidates_current_sample(
    app_client, session_factory, tmp_path, _demo_match
):
    await _decisions(app_client, session_factory, tmp_path, _demo_match)
    provenance = [
        {"ruleId": "R1", "ruleVersion": 1, "application": "INHERITED", "appliedFromPatch": "16.19"}
    ]
    async with session_factory() as session:
        marker = await session.get(
            MatchReconstructionModel, (_demo_match, RECONSTRUCTION_ALGORITHM_VERSION)
        )
        assert marker is not None
        version = marker.reconstruction_version
    async with session_factory.begin() as session:
        session.add(
            PatchRuleHealthModel(
                game_patch="DEMO-1",
                queue=None,
                application="INHERITED",
                source_patch="16.19",
                rules_fingerprint=fingerprint(provenance),
                reconstruction_version=version,
                clean_participants=35,
                failures=0,
                contributing_matches=3,
                wilson_upper=0.1,
                verdict="ACCEPT",
            )
        )
    await purge_match(
        session_factory,
        DurableRawMatchStore(tmp_path / "raw"),
        _demo_match,
        mode=ImportMode.DEMO,
        reason="USER_REQUEST",
        secret="integration-test-secret",
    )
    async with session_factory() as session:
        current = await health_for_state(session, "DEMO-1", version, provenance)
        assert current["status"] == "UNVERIFIED"
        assert current["verdict"] == "NO_SAMPLE"
    async with session_factory.begin() as session:
        await session.execute(
            delete(PatchRuleHealthModel).where(PatchRuleHealthModel.game_patch == "DEMO-1")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sequence", "expected_status", "expected_verdict"),
    [
        ([("REJECT", False), ("NO_SAMPLE", True)], "SUSPENDED", "NO_SAMPLE"),
        (
            [("REJECT", False), ("ACCEPT", False), ("NO_SAMPLE", True)],
            "UNVERIFIED",
            "NO_SAMPLE",
        ),
        ([("REJECT", False), ("NO_SAMPLE", False)], "UNVERIFIED", "NO_SAMPLE"),
        (
            [("REJECT", False), ("ACCEPT", False), ("NO_SAMPLE", False)],
            "UNVERIFIED",
            "NO_SAMPLE",
        ),
        ([("REJECT", False), ("REJECT", False)], "SUSPENDED", "REJECT"),
    ],
)
async def test_health_distinguishes_invalidation_from_real_measurement(
    session_factory, sequence, expected_status, expected_verdict
):
    patch = "TEST-SEQUENCE"
    provenance = [
        {
            "ruleId": "R1",
            "ruleVersion": 1,
            "application": "INHERITED",
            "appliedFromPatch": "16.19",
        }
    ]
    fp = fingerprint(provenance)
    version = RECONSTRUCTION_ALGORITHM_VERSION
    origin = datetime(2026, 10, 18, tzinfo=UTC)
    async with session_factory.begin() as session:
        await session.execute(
            delete(PatchRuleHealthModel).where(PatchRuleHealthModel.game_patch == patch)
        )
        for index, (outcome, invalidation) in enumerate(sequence):
            session.add(
                PatchRuleHealthModel(
                    game_patch=patch,
                    queue=None,
                    application="INHERITED",
                    source_patch="16.19",
                    rules_fingerprint=fp,
                    reconstruction_version=version,
                    clean_participants=35 if outcome != "NO_SAMPLE" else 0,
                    failures=8 if outcome == "REJECT" else 0,
                    contributing_matches=5 if outcome != "NO_SAMPLE" else 0,
                    wilson_upper=0.2 if outcome == "REJECT" else None,
                    verdict=outcome,
                    is_invalidation=invalidation,
                    computed_at=origin + timedelta(seconds=index),
                )
            )
    async with session_factory() as session:
        current = await health_for_state(session, patch, version, provenance)
    assert current["status"] == expected_status
    assert current["verdict"] == expected_verdict
    async with session_factory.begin() as session:
        await session.execute(
            delete(PatchRuleHealthModel).where(PatchRuleHealthModel.game_patch == patch)
        )


@pytest.mark.asyncio
async def test_health_invalidation_retains_only_matching_fingerprint_origin_and_version(
    session_factory,
):
    patch = "TEST-FILTER"
    provenance = [
        {
            "ruleId": "R1",
            "ruleVersion": 1,
            "application": "INHERITED",
            "appliedFromPatch": "16.19",
        }
    ]
    fp = fingerprint(provenance)
    version = RECONSTRUCTION_ALGORITHM_VERSION
    now = datetime(2026, 10, 18, tzinfo=UTC)
    async with session_factory.begin() as session:
        await session.execute(
            delete(PatchRuleHealthModel).where(PatchRuleHealthModel.game_patch == patch)
        )
        for source, row_fp, row_version in [
            ("16.19", "a" * 64, version),
            ("16.18", fp, version),
            ("16.19", fp, version + 1),
        ]:
            session.add(
                PatchRuleHealthModel(
                    game_patch=patch,
                    queue=None,
                    application="INHERITED",
                    source_patch=source,
                    rules_fingerprint=row_fp,
                    reconstruction_version=row_version,
                    clean_participants=35,
                    failures=8,
                    contributing_matches=5,
                    wilson_upper=0.2,
                    verdict="REJECT",
                    computed_at=now,
                )
            )
        session.add(
            PatchRuleHealthModel(
                game_patch=patch,
                queue=None,
                application="INHERITED",
                source_patch="16.19",
                rules_fingerprint=fp,
                reconstruction_version=version,
                clean_participants=0,
                failures=0,
                contributing_matches=0,
                wilson_upper=None,
                verdict="NO_SAMPLE",
                is_invalidation=True,
                computed_at=now + timedelta(seconds=1),
            )
        )
    async with session_factory() as session:
        current = await health_for_state(session, patch, version, provenance)
    assert current["status"] == "UNVERIFIED"
    assert current["verdict"] == "NO_SAMPLE"
    async with session_factory.begin() as session:
        await session.execute(
            delete(PatchRuleHealthModel).where(PatchRuleHealthModel.game_patch == patch)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0001"], indirect=True)
async def test_compute_and_purge_serialize_when_compute_has_read_the_sample(
    app_client, session_factory, tmp_path, _demo_match, monkeypatch
):
    await _decisions(app_client, session_factory, tmp_path, _demo_match)
    provenance = [
        {"ruleId": "R1", "ruleVersion": 1, "application": "INHERITED", "appliedFromPatch": "16.19"}
    ]
    async with session_factory.begin() as session:
        marker = await session.get(
            MatchReconstructionModel, (_demo_match, RECONSTRUCTION_ALGORITHM_VERSION)
        )
        assert marker is not None
        marker.rules_provenance = provenance

    compute_ready = asyncio.Event()
    release_compute = asyncio.Event()
    purge_lock_attempted = asyncio.Event()
    original_scalars = AsyncSession.scalars
    original_execute = AsyncSession.execute

    async def pause_after_sample_read(self, statement, *args, **kwargs):
        result = await original_scalars(self, statement, *args, **kwargs)
        if (
            asyncio.current_task().get_name() == "health-compute"
            and "participant_final_items" in str(statement)
        ):
            compute_ready.set()
            await release_compute.wait()
        return result

    async def track_purge_lock(self, statement, *args, **kwargs):
        params = args[0] if args and isinstance(args[0], dict) else kwargs.get("params") or {}
        if (
            asyncio.current_task().get_name() == "health-purge"
            and "pg_advisory_xact_lock" in str(statement)
            and params.get("key") == "rule-health:DEMO-1"
        ):
            purge_lock_attempted.set()
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "scalars", pause_after_sample_read)
    monkeypatch.setattr(AsyncSession, "execute", track_purge_lock)
    compute_task = asyncio.create_task(
        compute_health(session_factory, EmpiricalRules.disabled(), "DEMO-1"),
        name="health-compute",
    )
    await asyncio.wait_for(compute_ready.wait(), timeout=10)
    purge_task = asyncio.create_task(
        purge_match(
            session_factory,
            DurableRawMatchStore(tmp_path / "raw"),
            _demo_match,
            mode=ImportMode.DEMO,
            reason="USER_REQUEST",
            secret="integration-test-secret",
        ),
        name="health-purge",
    )
    await asyncio.wait_for(purge_lock_attempted.wait(), timeout=10)
    waiting = 0
    for _ in range(40):
        async with session_factory() as session:
            waiting = await session.scalar(
                text("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND NOT granted")
            )
        if waiting:
            break
        await asyncio.sleep(0)
    assert waiting > 0, "expurgo não ficou aguardando a trava mantida pelo recálculo"
    release_compute.set()
    await asyncio.gather(compute_task, purge_task)
    async with session_factory() as session:
        latest = await session.scalar(
            select(PatchRuleHealthModel)
            .where(
                PatchRuleHealthModel.game_patch == "DEMO-1", PatchRuleHealthModel.queue.is_(None)
            )
            .order_by(PatchRuleHealthModel.computed_at.desc(), PatchRuleHealthModel.id.desc())
            .limit(1)
        )
        assert latest is not None
        assert latest.verdict == "NO_SAMPLE"
        assert latest.is_invalidation is True
        assert latest.clean_participants == latest.contributing_matches == 0
    async with session_factory.begin() as session:
        await session.execute(
            delete(PatchRuleHealthModel).where(PatchRuleHealthModel.game_patch == "DEMO-1")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_health_changes_read_and_analysis_without_mutating_point(
    app_client, session_factory, tmp_path, _demo_match
):
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    provenance = [
        {"ruleId": "R1", "ruleVersion": 1, "application": "INHERITED", "appliedFromPatch": "16.19"}
    ]
    async with session_factory.begin() as session:
        point = await session.get(DecisionPointModel, uuid.UUID(decision["decisionId"]))
        state = await session.get(DecisionStateModel, point.decision_state_id)
        state.quality = {**state.quality, "empiricalRules": provenance}
        version = point.reconstruction_version
    for outcome, status, n, k in [
        ("NO_SAMPLE", "UNVERIFIED", 17, 0),
        ("ACCEPT", "OK", 35, 0),
        ("REJECT", "SUSPENDED", 35, 8),
    ]:
        async with session_factory.begin() as session:
            session.add(
                PatchRuleHealthModel(
                    game_patch="DEMO-1",
                    queue=None,
                    application="INHERITED",
                    source_patch="16.19",
                    rules_fingerprint=fingerprint(provenance),
                    reconstruction_version=version,
                    clean_participants=n,
                    failures=k,
                    contributing_matches=5,
                    wilson_upper=0.1,
                    verdict=outcome,
                )
            )
        detail = (await app_client.get("/api/v1/decisions/" + decision["decisionId"])).json()
        assert detail["versions"]["reconstructionRulesHealth"] == status
        assert detail["eligible"] is True
        assert "finalItems" not in detail
        listing = (
            await app_client.get(
                f"/api/v1/matches/{_demo_match}/decisions", params={"participantId": 1}
            )
        ).json()
        assert listing["items"][-1]["reconstructionRulesHealth"] == status
        analysis = await app_client.post(
            "/api/v1/analyses",
            json={"decisionId": decision["decisionId"], "objective": {"priority": "BALANCED"}},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert analysis.status_code == 201
        if status == "SUSPENDED":
            assert analysis.json()["status"] == "INSUFFICIENT_EVIDENCE"
            assert analysis.json()["support"]["reasonCodes"] == ["INHERITED_RULES_SUSPENDED"]
            async with session_factory.begin() as session:
                session.add(
                    PatchRuleHealthModel(
                        game_patch="DEMO-1",
                        queue=None,
                        application="INHERITED",
                        source_patch="16.18",
                        rules_fingerprint=fingerprint(provenance),
                        reconstruction_version=version,
                        clean_participants=35,
                        failures=0,
                        contributing_matches=5,
                        wilson_upper=0.01,
                        verdict="ACCEPT",
                    )
                )
            detail = (await app_client.get("/api/v1/decisions/" + decision["decisionId"])).json()
            assert detail["versions"]["reconstructionRulesHealth"] == "SUSPENDED"
        else:
            assert analysis.json()["status"] == "AVAILABLE"
            assert ("INHERITED_RULES_UNVERIFIED" in analysis.json()["support"]["reasonCodes"]) == (
                status == "UNVERIFIED"
            )
    await purge_match(
        session_factory,
        DurableRawMatchStore(tmp_path / "raw"),
        _demo_match,
        mode=ImportMode.DEMO,
        reason="USER_REQUEST",
        secret="integration-test-secret",
    )
    async with session_factory() as session:
        after_removal = await health_for_state(session, "DEMO-1", version, provenance)
    assert after_removal["status"] == "SUSPENDED"
    assert after_removal["verdict"] == "NO_SAMPLE"
    async with session_factory.begin() as session:
        await session.execute(
            delete(PatchRuleHealthModel).where(PatchRuleHealthModel.game_patch == "DEMO-1")
        )


@pytest.mark.asyncio
async def test_queue_health_suspends_only_its_queue_and_never_hides_a_queue_reject(
    session_factory,
):
    """CC-804: a medição da fila vale para a decisão da fila; o agregado só sem linha."""
    patch = "TEST-QUEUE"
    provenance = [
        {"ruleId": "R1", "ruleVersion": 1, "application": "INHERITED", "appliedFromPatch": "16.19"}
    ]
    fp = fingerprint(provenance)
    version = RECONSTRUCTION_ALGORITHM_VERSION
    now = datetime(2026, 10, 19, tzinfo=UTC)

    def row(queue, verdict, *, at, invalidation=False):
        sample = verdict != "NO_SAMPLE"
        return PatchRuleHealthModel(
            game_patch=patch,
            queue=queue,
            application="INHERITED",
            source_patch="16.19",
            rules_fingerprint=fp,
            reconstruction_version=version,
            clean_participants=200 if sample else 0,
            failures=(30 if verdict == "REJECT" else 2) if sample else 0,
            contributing_matches=60 if sample else 0,
            wilson_upper=(0.2 if verdict == "REJECT" else 0.03) if sample else None,
            verdict=verdict,
            is_invalidation=invalidation,
            computed_at=now + timedelta(seconds=at),
        )

    async with session_factory.begin() as session:
        await session.execute(
            delete(PatchRuleHealthModel).where(PatchRuleHealthModel.game_patch == patch)
        )
        session.add_all(
            [row(None, "ACCEPT", at=0), row(420, "ACCEPT", at=0), row(440, "REJECT", at=0)]
        )
    async with session_factory() as session:
        flex = await health_for_state(session, patch, version, provenance, 440)
        solo = await health_for_state(session, patch, version, provenance, 420)
        general = await health_for_state(session, patch, version, provenance)
    assert (flex["status"], flex["verdict"], flex["queue"]) == ("SUSPENDED", "REJECT", 440)
    assert (solo["status"], solo["queue"]) == ("OK", 420)
    assert (general["status"], general["queue"]) == ("OK", None)

    # Invalidação da fila retém o REJECT só dela; a outra fila não herda a retenção.
    async with session_factory.begin() as session:
        session.add_all(
            [
                row(440, "NO_SAMPLE", at=1, invalidation=True),
                row(420, "NO_SAMPLE", at=1, invalidation=True),
            ]
        )
    async with session_factory() as session:
        flex = await health_for_state(session, patch, version, provenance, 440)
        solo = await health_for_state(session, patch, version, provenance, 420)
    assert flex["status"] == "SUSPENDED" and flex["verdict"] == "NO_SAMPLE"
    assert solo["status"] == "UNVERIFIED"

    # Histórico sem linha da fila: vale o agregado do grupo.
    async with session_factory() as session:
        other = await health_for_state(session, patch, version + 1, provenance, 440)
    assert other["queue"] is None and other["status"] == "UNVERIFIED"
    async with session_factory.begin() as session:
        await session.execute(
            delete(PatchRuleHealthModel).where(PatchRuleHealthModel.game_patch == patch)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_flex_decision_warns_rules_measured_in_solo_until_flex_sample_accepts(
    app_client, session_factory, tmp_path, _demo_match
):
    """CC-804 parte A: regras sem domínio da fila 440 aplicadas no Flex levam aviso enquanto
    a saúde do Flex não tiver ACCEPT próprio; REJECT do Flex suspende o Flex."""
    decision = (await _decisions(app_client, session_factory, tmp_path, _demo_match))[-1]
    provenance = [
        {"ruleId": "R1", "ruleVersion": 1, "application": "INHERITED", "appliedFromPatch": "16.19"}
    ]
    async with session_factory.begin() as session:
        point = await session.get(DecisionPointModel, uuid.UUID(decision["decisionId"]))
        state = await session.get(DecisionStateModel, point.decision_state_id)
        state.quality = {**state.quality, "empiricalRules": provenance}
        version = point.reconstruction_version
        match = await session.get(MatchModel, _demo_match)
        match.queue = 440
    try:
        for outcome, n, k, upper in [
            ("NO_SAMPLE", 17, 1, 0.27),
            ("ACCEPT", 120, 1, 0.04),
            ("REJECT", 200, 30, 0.2),
        ]:
            async with session_factory.begin() as session:
                session.add(
                    PatchRuleHealthModel(
                        game_patch="DEMO-1",
                        queue=440,
                        application="INHERITED",
                        source_patch="16.19",
                        rules_fingerprint=fingerprint(provenance),
                        reconstruction_version=version,
                        clean_participants=n,
                        failures=k,
                        contributing_matches=14,
                        wilson_upper=upper,
                        verdict=outcome,
                    )
                )
            analysis = await app_client.post(
                "/api/v1/analyses",
                json={"decisionId": decision["decisionId"], "objective": {"priority": "BALANCED"}},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
            codes = analysis.json()["support"]["reasonCodes"]
            if outcome == "REJECT":
                assert analysis.json()["status"] == "INSUFFICIENT_EVIDENCE"
                assert codes == ["INHERITED_RULES_SUSPENDED"]
            else:
                assert analysis.json()["status"] == "AVAILABLE"
                assert ("RULES_MEASURED_IN_SOLO_DUO" in codes) == (outcome == "NO_SAMPLE")
    finally:
        async with session_factory.begin() as session:
            match = await session.get(MatchModel, _demo_match)
            match.queue = 420
            await session.execute(
                delete(PatchRuleHealthModel).where(PatchRuleHealthModel.game_patch == "DEMO-1")
            )
