"""DurableRawMatchStore (CC-201): armazenamento durável de match+timeline
brutos, distinto do manifesto JSON do spike CC-105 (nunca tocado aqui).
Publicação imutável endereçada por conteúdo (blobs/<sha256>.json) + ponteiro
atômico (<id>.<kind>.ref) — corrigido na revisão CC-201: os dois
os.replace() antigos (corpo, depois sidecar de hash) podiam intercalar com
outro escritor, deixando corpo de um e hash de outro; e store() sempre
sobrescrevia sem checar o estado anterior, permitindo um dono antigo do
lease publicar por cima do dono novo silenciosamente."""

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

    top_level_names = sorted(p.name for p in directory.iterdir())
    assert top_level_names == ["DEMO-0001.timeline.ref", "blobs"]
    assert (directory / "DEMO-0001.timeline.ref").read_text().strip() == _sha(body)

    blob_names = sorted(p.name for p in (directory / "blobs").iterdir())
    assert blob_names == [f"{_sha(body)}.json"]
    assert not any(name.endswith(".tmp") or ".tmp-" in name for name in blob_names)


def test_store_rejects_mismatched_sha256(tmp_path) -> None:
    store = DurableRawMatchStore(tmp_path / "raw")
    with pytest.raises(ValueError):
        store.store("BR1_123", "match", b"corpo real", "sha256-errado-nao-confere")


def test_store_same_content_twice_is_idempotent(tmp_path) -> None:
    """Leitura idempotente de bytes iguais (revisão CC-201): o mesmo
    conteúdo pode ser publicado mais de uma vez (ex.: worker retomado após
    crash refaz o fetch e chega ao mesmo resultado real) sem erro."""
    store = DurableRawMatchStore(tmp_path / "raw")
    body = b'{"a": 1}'
    store.store("BR1_123", "match", body, _sha(body))
    store.store("BR1_123", "match", body, _sha(body))  # não levanta
    assert store.read_if_valid("BR1_123", "match") == body


def test_store_rejects_different_content_for_same_identity_never_overwrites(tmp_path) -> None:
    """Achado real da revisão CC-201 (existing_raw_silently_replaced=True no
    probe): store() sobrescrevia sempre. Agora a segunda publicação com
    conteúdo diferente é rejeitada, e o conteúdo original permanece
    recuperável — nunca last-write-wins."""
    store = DurableRawMatchStore(tmp_path / "raw")
    store.store("BR1_123", "match", b"old", _sha(b"old"))

    with pytest.raises(RawStoreCorruptedError):
        store.store("BR1_123", "match", b"new", _sha(b"new"))

    assert store.read_if_valid("BR1_123", "match") == b"old"


def test_late_write_from_stale_lease_owner_never_overwrites_current_owner(tmp_path) -> None:
    """Simula o cenário concreto da revisão: o dono novo do lease publica
    primeiro; o dono antigo (que só percebe a perda de posse depois) tenta
    publicar seu próprio fetch, com bytes potencialmente diferentes —
    rejeitado, o conteúdo do dono atual nunca é substituído."""
    store = DurableRawMatchStore(tmp_path / "raw")
    current_owner_body = b'{"from": "current-owner"}'
    stale_owner_body = b'{"from": "stale-owner"}'

    store.store("BR1_999", "match", current_owner_body, _sha(current_owner_body))
    with pytest.raises(RawStoreCorruptedError):
        store.store("BR1_999", "match", stale_owner_body, _sha(stale_owner_body))

    assert store.read_if_valid("BR1_999", "match") == current_owner_body


def test_corrupted_blob_is_detected_and_never_silently_overwritten(tmp_path) -> None:
    directory = tmp_path / "raw"
    store = DurableRawMatchStore(directory)
    body = b'{"a": 1}'
    store.store("BR1_123", "match", body, _sha(body))

    # Simula corrupcao externa (bit rot, edicao manual): o BLOB (endereçado
    # pelo hash original) passa a conter bytes que não batem com seu próprio
    # nome.
    blob_path = directory / "blobs" / f"{_sha(body)}.json"
    blob_path.write_bytes(b'{"a": 2}')

    with pytest.raises(RawStoreCorruptedError):
        store.read_if_valid("BR1_123", "match")


def test_ref_pointing_to_missing_blob_is_detected(tmp_path) -> None:
    """Ponteiro presente mas o blob referenciado sumiu do disco (ex.:
    limpeza externa indevida) — nunca finge ausência nem re-busca por cima
    sem avisar."""
    directory = tmp_path / "raw"
    store = DurableRawMatchStore(directory)
    body = b'{"a": 1}'
    store.store("BR1_123", "match", body, _sha(body))

    (directory / "blobs" / f"{_sha(body)}.json").unlink()

    with pytest.raises(RawStoreCorruptedError):
        store.read_if_valid("BR1_123", "match")


def test_ref_with_malformed_hash_is_detected(tmp_path) -> None:
    directory = tmp_path / "raw"
    store = DurableRawMatchStore(directory)
    (directory / "blobs").mkdir(parents=True, exist_ok=True)
    (directory / "BR1_123.match.ref").write_text("nao-e-um-hash-sha256-valido")

    with pytest.raises(RawStoreCorruptedError):
        store.read_if_valid("BR1_123", "match")


def test_orphaned_blob_without_ref_is_recoverable_on_next_store(tmp_path) -> None:
    """ "Falha entre passos" (revisão CC-201): crash logo após o blob ser
    gravado mas antes do ponteiro ser atualizado deixa um blob órfão,
    inofensivo — a próxima store() do mesmo conteúdo real completa a
    publicação (reconhece o blob já existente, só falta o ponteiro), sem
    nova gravação do corpo nem erro."""
    directory = tmp_path / "raw"
    store = DurableRawMatchStore(directory)
    body = b'{"a": 1}'

    # Simula o estado pós-crash: blob gravado, ponteiro nunca chegou a
    # existir (equivalente ao que store() faria até o meio do caminho).
    (directory / "blobs").mkdir(parents=True, exist_ok=True)
    (directory / "blobs" / f"{_sha(body)}.json").write_bytes(body)
    assert store.read_if_valid("BR1_123", "match") is None  # sem ponteiro, nunca "gravado"

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
    assert (tmp_path / "nested" / "raw" / "blobs" / f"{_sha(body)}.json").exists()
    assert (tmp_path / "nested" / "raw" / "BR1_1.match.ref").exists()


def test_two_writers_different_content_deterministic_interleaving(tmp_path) -> None:
    """Interleaving determinístico de dois escritores com bytes diferentes
    para a mesma identidade (revisão CC-201): quem publica o ponteiro
    primeiro vence; o segundo é rejeitado e o conteúdo do primeiro
    permanece íntegro e recuperável, independente da ordem exata dos passos
    internos de cada um (blob é sempre gravado antes do ponteiro)."""
    directory = tmp_path / "raw"
    writer_a = DurableRawMatchStore(directory)
    writer_b = DurableRawMatchStore(directory)
    body_a = b'{"writer": "a"}'
    body_b = b'{"writer": "b"}'

    writer_a.store("BR1_555", "match", body_a, _sha(body_a))
    with pytest.raises(RawStoreCorruptedError):
        writer_b.store("BR1_555", "match", body_b, _sha(body_b))

    # Os dois blobs foram gravados (endereçados por conteúdo, sem conflito
    # entre si); só a publicação do ponteiro é exclusiva.
    assert (directory / "blobs" / f"{_sha(body_a)}.json").read_bytes() == body_a
    assert (directory / "blobs" / f"{_sha(body_b)}.json").read_bytes() == body_b
    assert writer_a.read_if_valid("BR1_555", "match") == body_a
    assert writer_b.read_if_valid("BR1_555", "match") == body_a
