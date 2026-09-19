"""Cursor opaco e projeção pública de motivos (CC-204). Puro, sem
banco: o comportamento com o banco está em tests/integration/test_decision_routes.py."""

from __future__ import annotations

import base64
import json
import uuid

import pytest

from riftlab.decisions.queries import (
    MAX_CURSOR_CHARS,
    PUBLIC_TARGET_REASON,
    CursorPosition,
    decode_cursor,
    encode_cursor,
    public_reasons,
)
from riftlab.shared.errors import InvalidCursorError

POSITION = CursorPosition(
    extraction_id=uuid.UUID(int=11),
    match_id="DEMO-0002",
    participant_id=1,
    timestamp_ms=480_000,
    source_index=7,
    point_id=uuid.UUID(int=22),
)


def _b64(payload: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()


def _fields(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "v": 1,
        "x": str(POSITION.extraction_id),
        "m": POSITION.match_id,
        "p": 1,
        "t": 480_000,
        "s": 7,
        "i": str(POSITION.point_id),
    }
    base.update(overrides)
    return base


def test_cursor_round_trips_and_is_opaque_url_safe_text() -> None:
    cursor = encode_cursor(POSITION)
    assert cursor and "=" not in cursor and "/" not in cursor and "+" not in cursor
    assert decode_cursor(cursor, match_id="DEMO-0002", participant_id=1) == POSITION
    assert "DEMO-0002" not in cursor  # opaco: não legível a olho


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "!!!",
        "A" * (MAX_CURSOR_CHARS + 1),
        "çãõ",
        base64.urlsafe_b64encode(b"nao json").decode(),
        _b64([1]),
        _b64({}),
        _b64(_fields(v=2)),
        _b64(_fields(v=True)),
        _b64({k: v for k, v in _fields().items() if k != "s"}),
        _b64({**_fields(), "z": 1}),
        _b64(_fields(p="1")),
        _b64(_fields(p=True)),
        _b64(_fields(t=-1)),
        _b64(_fields(s=2**31)),
        _b64(_fields(x="nao-uuid")),
        _b64(_fields(x=str(uuid.UUID(int=11)).upper())),  # só a forma canônica
        _b64(_fields(i=5)),
        _b64(_fields(m="DEMO-0008")),  # outra partida
        _b64(_fields(p=6)),  # outro participante
    ],
)
def test_malformed_tampered_or_foreign_cursor_is_rejected_without_echo(raw: str) -> None:
    with pytest.raises(InvalidCursorError) as excinfo:
        decode_cursor(raw, match_id="DEMO-0002", participant_id=1)
    assert raw[:20] not in str(excinfo.value) or not raw


def test_target_reasons_collapse_into_one_neutral_reason_without_sub_reasons() -> None:
    reasons = [
        {"code": "TARGET_NOT_CLASSIFIABLE", "subReasons": ["UNKNOWN", "ITEM_NOT_IN_CATALOG"]},
        {"code": "TARGET_REVERTED", "subReasons": ["REVERTED_BY_UNDO"]},
        {"code": "MISSING_CATALOG", "subReasons": ["NO_CATALOG"]},
        {"code": "TARGET_STATUS_UNCERTAIN", "subReasons": ["STATUS_UNCERTAIN"]},
        {"code": "INVENTORY_UNCERTAIN", "subReasons": ["UNVALIDATED_UNDO_SEMANTICS"]},
    ]
    public = public_reasons(reasons)
    assert [(r.code, r.sub_reasons) for r in public] == [
        (PUBLIC_TARGET_REASON, ()),
        ("MISSING_CATALOG", ("NO_CATALOG",)),
        ("INVENTORY_UNCERTAIN", ("UNVALIDATED_UNDO_SEMANTICS",)),  # contexto: literal
    ]


def test_no_reasons_stays_empty() -> None:
    assert public_reasons([]) == []
