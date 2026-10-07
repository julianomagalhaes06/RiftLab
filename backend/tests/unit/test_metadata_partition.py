from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from riftlab.collection.metadata_partition import (
    StructuralMetadata,
    StructuralMetadataError,
    build_partition,
    parse_structural_metadata,
)

SECRET = "unit-test-secret"


def _match_body(match_id: str, puuids: list[str], **overrides: object) -> bytes:
    metadata: dict[str, object] = {"matchId": match_id, "participants": puuids}
    info: dict[str, object] = {
        "gameVersion": "16.19.1",
        "queueId": 420,
        "mapId": 11,
        "endOfGameResult": "GameComplete",
        "participants": [{"win": True, "items": [9999], "summonerName": "PRIVATE"}],
        "teams": [{"win": True}],
    }
    root: dict[str, object] = {
        "metadata": metadata,
        "info": info,
        "timeline": {"frames": [{"events": [{"type": "ITEM_PURCHASED", "itemId": 9999}]}]},
    }
    root.update(overrides)
    return json.dumps(root, separators=(",", ":")).encode()


def _record(match_id: str, participants: tuple[str, ...], **changes: object) -> StructuralMetadata:
    values: dict[str, object] = {
        "match_id": match_id,
        "patch": "16.19",
        "queue_id": 420,
        "map_id": 11,
        "game_complete": True,
        "participant_hmacs": participants,
    }
    values.update(changes)
    return StructuralMetadata(**values)  # type: ignore[arg-type]


def test_projection_extracts_only_allowlist_and_hashes_participants() -> None:
    body = _match_body("BR1_123456", ["puuid-private-a", "puuid-private-b"])

    result = parse_structural_metadata(
        body, requested_match_id="BR1_123456", subject_hash_secret=SECRET
    )

    expected = hmac.new(
        SECRET.encode(), b"CC-316-participant-v1|puuid-private-a", hashlib.sha256
    ).hexdigest()
    assert result.match_id == "BR1_123456"
    assert result.patch == "16.19"
    assert result.queue_id == 420
    assert result.map_id == 11
    assert result.game_complete
    assert expected in result.participant_hmacs
    assert "puuid-private-a" not in repr(result)
    assert b"PRIVATE" in body  # fixture proves forbidden content was present in input


def test_projection_does_not_decode_forbidden_scalar_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoded: list[str] = []
    real_loads = json.loads

    def track_loads(value: str | bytes | bytearray, *args: object, **kwargs: object) -> object:
        if isinstance(value, str):
            decoded.append(value)
        return real_loads(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", track_loads)
    parse_structural_metadata(
        _match_body("BR1_123456", ["puuid-private-a"]),
        requested_match_id="BR1_123456",
        subject_hash_secret=SECRET,
    )

    assert not any("PRIVATE" in value or "9999" in value for value in decoded)


def test_participant_callback_is_explicit_and_projection_keeps_only_hmacs() -> None:
    discovered: list[str] = []

    result = parse_structural_metadata(
        _match_body("BR1_123456", ["puuid-private-a"]),
        requested_match_id="BR1_123456",
        subject_hash_secret=SECRET,
        participant_callback=discovered.append,
    )

    assert discovered == ["puuid-private-a"]
    assert "puuid-private-a" not in repr(result)


def test_participant_callback_runs_only_after_complete_document_validation() -> None:
    discovered: list[str] = []
    body = _match_body("BR1_123456", ["puuid-private-a"]) + b" trailing"

    with pytest.raises(StructuralMetadataError):
        parse_structural_metadata(
            body,
            requested_match_id="BR1_123456",
            subject_hash_secret=SECRET,
            participant_callback=discovered.append,
        )

    assert discovered == []


def test_projection_rejects_malformed_opaque_string() -> None:
    body = _match_body("BR1_123456", ["puuid-private-a"]).replace(b"PRIVATE", b"\\xPRIVATE")

    with pytest.raises(StructuralMetadataError):
        parse_structural_metadata(body, requested_match_id="BR1_123456", subject_hash_secret=SECRET)


@pytest.mark.parametrize(
    ("body", "requested"),
    [
        (_match_body("BR1_2", ["p"]), "BR1_1"),
        (_match_body("BR1_2", ["p"], info={"gameVersion": "16.19.1"}), "BR1_2"),
        (b'{"metadata":{"matchId":"BR1_2","participants":["p"]},"info":', "BR1_2"),
        (
            b'{"metadata":{"matchId":"BR1_2","matchId":"BR1_2","participants":["p"]},"info":{"gameVersion":"16.19.1","queueId":420,"mapId":11,"endOfGameResult":"GameComplete"}}',
            "BR1_2",
        ),
    ],
)
def test_projection_fails_closed(body: bytes, requested: str) -> None:
    with pytest.raises(StructuralMetadataError):
        parse_structural_metadata(body, requested_match_id=requested, subject_hash_secret=SECRET)


def test_partition_is_deterministic_and_excludes_overlapping_confirmation_without_replacement() -> (
    None
):
    rows = [_record(f"BR1_{n}", (f"p{n}",)) for n in range(1, 24)]
    # Add an overlap to a would-be confirmation record without changing its ID ordering.
    ordered = sorted(
        rows,
        key=lambda row: hashlib.sha256(f"CC-316-partition-v1|{row.match_id}".encode()).hexdigest(),
    )
    overlap_target = ordered[21]
    rows[rows.index(overlap_target)] = _record(
        overlap_target.match_id, ordered[0].participant_hmacs
    )

    first = build_partition(rows)
    second = build_partition(list(reversed(rows)))

    assert first == second
    assert first.status == "READY"
    assert len(first.exploration_ids) == 20
    assert first.overlap_exclusions == (overlap_target.match_id,)
    assert len(first.confirmation_ids) == 2
    assert overlap_target.match_id not in first.confirmation_ids


def test_partition_reports_no_sample_and_structural_exclusions() -> None:
    records = [
        _record("BR1_1", ("p1",), patch="16.18"),
        _record("BR1_2", ("p2",)),
    ]

    result = build_partition(records)

    assert result.status == "NO_SAMPLE"
    assert result.exploration_ids == ("BR1_2",)
    assert result.confirmation_ids == ()
    assert result.structural_exclusions == ("BR1_1",)
