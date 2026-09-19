"""Cursor de GET /matches (CC-205): opaco, versionado e sempre validado; nunca
ecoa o conteúdo recebido."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from riftlab.matches.queries import decode_match_cursor, encode_match_cursor
from riftlab.shared.errors import InvalidCursorError


def _raw(payload: object) -> str:
    data = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@pytest.mark.parametrize("match_id", ["BR1_1", "BR1_3120345678", "DEMO-0008"])
def test_cursor_round_trips_time_and_match_id_including_microseconds_and_offset(
    match_id: str,
) -> None:
    for start in (
        datetime(2026, 9, 1, 12, 30, 15, 987654, tzinfo=UTC),
        datetime(2026, 9, 1, 9, 0, tzinfo=timezone(timedelta(hours=-3))),
    ):
        decoded = decode_match_cursor(encode_match_cursor(start, match_id))
        assert decoded == (start, match_id)
        assert decoded[0].utcoffset() == start.utcoffset()


def test_encoded_cursor_is_url_safe_and_unpadded() -> None:
    cursor = encode_match_cursor(datetime(2026, 9, 1, tzinfo=UTC), "BR1_1")
    assert cursor.isascii() and "=" not in cursor and "+" not in cursor and "/" not in cursor


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "!!!",
        "bm90LWpzb24",
        _raw([]),
        _raw({"v": 1, "t": "2026-09-01T00:00:00+00:00"}),
        _raw({"v": 1, "t": "2026-09-01T00:00:00", "m": "BR1_1"}),
        _raw({"v": 1, "t": "hoje", "m": "BR1_1"}),
        _raw({"v": 2, "t": "2026-09-01T00:00:00+00:00", "m": "BR1_1"}),
        _raw({"v": 1, "t": "2026-09-01T00:00:00+00:00", "m": "br1_1"}),
        _raw({"v": 1, "t": "2026-09-01T00:00:00+00:00", "m": 7}),
        _raw({"v": 1, "t": "2026-09-01T00:00:00+00:00", "m": "BR1_1", "extra": 1}),
        "ç" * 10,
        "A" * 257,
    ],
)
def test_malformed_cursor_is_rejected_with_fixed_message(cursor: str) -> None:
    with pytest.raises(InvalidCursorError) as excinfo:
        decode_match_cursor(cursor)
    assert str(excinfo.value) == "cursor inválido"
