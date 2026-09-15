"""DurableRawMatchStore (CC-201): armazenamento durável de match+timeline
brutos, distinto do manifesto JSON do spike CC-105 (nunca tocado aqui).
Gravação atômica (temp + os.replace), detecção de corrupção sem sobrescrita
automática, validação de match_id independente do chamador."""

from __future__ import annotations

import hashlib

import pytest

from riftlab.ingestion.infrastructure.raw_store import (
    DurableRawMatchStore,
    RawStoreCorruptedError,
)


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def test_read_if_valid_returns_none_when_never_stored(tmp_path) -> None:
    store = DurableRawMatchStore(tmp_path / "raw")
    assert store.read_if_valid("BR1_123", "match") is None


def test_store_then_read_if_valid_round_trips(tmp_path) -> None:
    store = DurableRawMatchStore(tmp_path / "raw")
    body = b'{"metadata": {"matchId": "BR1_123"}}'
    store.store("BR1_123", "match", body, _sha(body))
    assert store.read_if_valid("BR1_123", "match") == body


def test_store_writes_atomically_leaving_no_temp_files(tmp_path) -> None:
    directory = tmp_path / "raw"
    store = DurableRawMatchStore(directory)
    body = b'{"a": 1}'
    store.store("DEMO-0001", "timeline", body, _sha(body))

    names = sorted(p.name for p in directory.iterdir())
    assert names == ["DEMO-0001.timeline.json", "DEMO-0001.timeline.sha256"]
    assert (directory / "DEMO-0001.timeline.sha256").read_text().strip() == _sha(body)


def test_store_rejects_mismatched_sha256(tmp_path) -> None:
    store = DurableRawMatchStore(tmp_path / "raw")
    with pytest.raises(ValueError):
        store.store("BR1_123", "match", b"corpo real", "sha256-errado-nao-confere")


def test_corrupted_body_is_detected_and_never_silently_overwritten(tmp_path) -> None:
    directory = tmp_path / "raw"
    store = DurableRawMatchStore(directory)
    body = b'{"a": 1}'
    store.store("BR1_123", "match", body, _sha(body))

    # Simula corrupcao externa (bit rot, edicao manual): bytes divergem do
    # hash gravado ao lado.
    (directory / "BR1_123.match.json").write_bytes(b'{"a": 2}')

    with pytest.raises(RawStoreCorruptedError):
        store.read_if_valid("BR1_123", "match")

    # store() de novo com o corpo original ainda funciona (gravacao atomica
    # nao depende do estado anterior estar integro).
    store.store("BR1_123", "match", body, _sha(body))
    assert store.read_if_valid("BR1_123", "match") == body


def test_match_and_timeline_are_independent_for_same_match_id(tmp_path) -> None:
    store = DurableRawMatchStore(tmp_path / "raw")
    match_body = b'{"kind": "match"}'
    timeline_body = b'{"kind": "timeline"}'
    store.store("BR1_999", "match", match_body, _sha(match_body))
    store.store("BR1_999", "timeline", timeline_body, _sha(timeline_body))

    assert store.read_if_valid("BR1_999", "match") == match_body
    assert store.read_if_valid("BR1_999", "timeline") == timeline_body


@pytest.mark.parametrize(
    "offending_match_id",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "BR1_1;DROP TABLE",
        "DEMO-1",
        "http://evil.example/BR1_1",
    ],
)
def test_rejects_unsafe_or_malformed_match_id(tmp_path, offending_match_id: str) -> None:
    store = DurableRawMatchStore(tmp_path / "raw")
    with pytest.raises(ValueError):
        store.read_if_valid(offending_match_id, "match")
    with pytest.raises(ValueError):
        store.store(offending_match_id, "match", b"x", _sha(b"x"))


def test_rejects_unknown_kind(tmp_path) -> None:
    store = DurableRawMatchStore(tmp_path / "raw")
    with pytest.raises(ValueError):
        store.read_if_valid("BR1_123", "frames")


def test_directory_created_lazily_and_survives_relative_tmp_path(tmp_path, monkeypatch) -> None:
    """Nunca depende do cwd: construído com um Path relativo qualquer, ainda
    funciona porque Path.mkdir/writes resolvem a partir do Path fornecido,
    não de Path.cwd()."""
    monkeypatch.chdir(tmp_path)
    store = DurableRawMatchStore(tmp_path / "nested" / "raw")
    body = b"{}"
    store.store("BR1_1", "match", body, _sha(body))
    assert (tmp_path / "nested" / "raw" / "BR1_1.match.json").exists()
