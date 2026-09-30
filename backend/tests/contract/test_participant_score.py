from __future__ import annotations

import json
from dataclasses import fields

import pytest
from pydantic import ValidationError

from riftlab.ingestion.infrastructure.demo_source import (
    DemoMatchSource,
    parse_match_source_document,
)
from riftlab.ingestion.score import ParticipantScore, parse_participant_score
from riftlab.matches.queries import MatchParticipantFactsView
from riftlab.shared.api.matches import MatchParticipant
from riftlab.shared.errors import SourceSchemaError


async def test_demo_known_zero_and_absent_score() -> None:
    result = await DemoMatchSource().load_match("DEMO-0001")
    assert result.participants[0].score == ParticipantScore(
        win=True,
        kills=3,
        deaths=0,
        assists=5,
        total_minions_killed=120,
        neutral_minions_killed=4,
    )
    assert result.participants[1].score == ParticipantScore()
    assert "subject_hash" not in {field.name for field in fields(MatchParticipantFactsView)}


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("win", 1),
        ("win", "true"),
        ("kills", True),
        ("deaths", -1),
        ("assists", 1.5),
        ("totalMinionsKilled", "0"),
        ("neutralMinionsKilled", 2_147_483_648),
    ],
)
def test_invalid_score_is_rejected_by_shared_parser_and_http_contract(
    field: str, invalid: object
) -> None:
    with pytest.raises(SourceSchemaError) as error:
        parse_participant_score({field: invalid})
    assert error.value.code == "MATCH_SCORE_INVALID"
    api_field = {
        "totalMinionsKilled": "totalMinionsKilled",
        "neutralMinionsKilled": "neutralMinionsKilled",
    }.get(field, field)
    with pytest.raises(ValidationError):
        MatchParticipant.model_validate(
            {"participantId": 1, "championId": 1, "team": 100, api_field: invalid}
        )


def test_demo_invalid_score_rejects_document_without_defaulting_to_zero() -> None:
    raw = {
        "schemaVersion": "v1",
        "matchId": "DEMO-0001",
        "queue": 420,
        "mapId": 11,
        "patch": "DEMO-1",
        "startAt": "2026-01-01T00:00:00+00:00",
        "durationSeconds": 1,
        "participants": [{"participantId": 1, "championId": 1, "team": 100, "deaths": False}],
        "frames": [],
    }
    with pytest.raises(SourceSchemaError) as error:
        parse_match_source_document("DEMO-0001", json.dumps(raw).encode())
    assert error.value.code == "MATCH_SCORE_INVALID"
