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
import os
import threading
from unittest import mock

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


def test_legacy_format_is_migrated_non_destructively_not_treated_as_never_collected(
    tmp_path,
) -> None:
    """Compatibilidade com o formato anterior à correção CC-201 (commit
    63f638a: `<id>.<kind>.json` + sidecar `.sha256`, sem blobs/). Um
    diretório com dados nesse formato nunca é tratado como cache vazio e
    silenciosamente substituído por nova coleta de rede — read_if_valid
    migra para o esquema atual na primeira leitura, sem apagar os arquivos
    antigos (procedimento não destrutivo)."""
    directory = tmp_path / "raw"
    directory.mkdir(parents=True)
    body = b'{"legacy": true}'
    (directory / "BR1_111.match.json").write_bytes(body)
    (directory / "BR1_111.match.sha256").write_text(_sha(body))

    store = DurableRawMatchStore(directory)
    assert store.read_if_valid("BR1_111", "match") == body  # nunca None

    # Arquivos antigos preservados (nunca apagados automaticamente) e o
    # esquema novo agora também existe, sem nova coleta de rede.
    assert (directory / "BR1_111.match.json").read_bytes() == body
    assert (directory / "BR1_111.match.sha256").read_text() == _sha(body)
    assert (directory / "BR1_111.match.ref").read_text().strip() == _sha(body)
    assert (directory / "blobs" / f"{_sha(body)}.json").read_bytes() == body


def test_legacy_format_with_corrupted_sidecar_is_detected_not_migrated(tmp_path) -> None:
    """Sidecar .sha256 divergente do corpo legado: nunca migrado
    silenciosamente como se fosse íntegro — resolução manual exigida, mesma
    postura do formato atual."""
    directory = tmp_path / "raw"
    directory.mkdir(parents=True)
    (directory / "BR1_222.match.json").write_bytes(b'{"legacy": true}')
    (directory / "BR1_222.match.sha256").write_text(_sha(b"conteudo diferente"))

    store = DurableRawMatchStore(directory)
    with pytest.raises(RawStoreCorruptedError):
        store.read_if_valid("BR1_222", "match")


@pytest.mark.parametrize("missing", ["hash", "body"])
def test_legacy_format_partial_pair_is_never_treated_as_absent(tmp_path, missing: str) -> None:
    """Achado real da revisão complementar (CC-201-REVIEW-3,
    legacy_partial_treated_as_absent=True no probe): um par legado com só
    metade presente (corpo sem sidecar, ou sidecar sem corpo — ex.: crash no
    meio da escrita antiga) era confundido com "nunca coletado" e liberava
    nova coleta de rede em cima de dado local já parcialmente presente.
    Agora sempre RawStoreCorruptedError, nunca None."""
    directory = tmp_path / "raw"
    directory.mkdir(parents=True)
    if missing != "body":
        (directory / "BR1_333.match.json").write_bytes(b'{"legacy": true}')
    if missing != "hash":
        (directory / "BR1_333.match.sha256").write_text(_sha(b'{"legacy": true}'))

    store = DurableRawMatchStore(directory)
    with pytest.raises(RawStoreCorruptedError):
        store.read_if_valid("BR1_333", "match")

    # Nunca migrado/sobrescrito por causa da leitura que falhou: nenhum
    # arquivo do esquema novo foi criado.
    assert not (directory / "BR1_333.match.ref").exists()
    assert not (directory / "blobs").exists() or not any((directory / "blobs").iterdir())


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


def test_two_writers_different_content_sequential_rejection(tmp_path) -> None:
    """Chamadas sequenciais (não concorrência real — ver o teste de barreira
    abaixo para isso): quem publica o ponteiro primeiro vence; o segundo é
    rejeitado e o conteúdo do primeiro permanece íntegro e recuperável."""
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


def _patched_link_pausing_before_ref_publish(barrier: threading.Barrier):
    """Intercepta os.link (usado por _publish_once) e trava as duas threads
    exatamente antes de qualquer uma publicar o ponteiro `.ref` — força a
    janela de corrida real que a checagem `if ref_path.exists()` sozinha não
    cobria (revisão CC-201, achado 1: os.replace sempre substitui; dois
    escritores que checam ausência no mesmo instante os dois "venciam").
    Escritas de blob (endereçadas por hash, nunca em conflito entre si)
    passam direto, sem barreira."""
    original_link = os.link

    def paused_link(src, dst, *args, **kwargs):
        if str(dst).endswith(".ref"):
            barrier.wait(timeout=5)
        return original_link(src, dst, *args, **kwargs)

    return paused_link


def test_concurrent_divergent_writers_real_barrier_never_both_succeed(tmp_path) -> None:
    """Concorrência REAL (duas threads de verdade, barreira imediatamente
    antes da publicação exclusiva do ponteiro) — não sequencial. Antes desta
    correção (os.replace + `if not path.exists()`), os dois escritores
    observavam ausência no mesmo instante e os dois retornavam sucesso com
    hashes diferentes (probe da revisão: concurrent_divergent_writers=
    ['SUCCESS', 'SUCCESS']). Com publicação write-once (hard link exclusivo),
    no máximo um dos dois pode ter publicado — nunca os dois."""
    directory = tmp_path / "raw"
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def writer(name: str, body: bytes) -> None:
        try:
            DurableRawMatchStore(directory).store("BR1_777", "match", body, _sha(body))
            results[name] = "SUCCESS"
        except RawStoreCorruptedError:
            results[name] = "REJECTED"

    with mock.patch.object(
        os, "link", side_effect=_patched_link_pausing_before_ref_publish(barrier)
    ):
        t1 = threading.Thread(target=writer, args=("a", b"first"))
        t2 = threading.Thread(target=writer, args=("b", b"second"))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

    assert sorted(results.values()) == ["REJECTED", "SUCCESS"], results
    winner_body = b"first" if results["a"] == "SUCCESS" else b"second"
    assert DurableRawMatchStore(directory).read_if_valid("BR1_777", "match") == winner_body


def test_concurrent_identical_writers_real_barrier_both_succeed_idempotent(tmp_path) -> None:
    """Mesma barreira real, mas com bytes IGUAIS: idempotência preservada sob
    concorrência real, não só em chamadas sequenciais — os dois escritores
    terminam sem exceção e o conteúdo publicado é o mesmo dos dois."""
    directory = tmp_path / "raw"
    barrier = threading.Barrier(2)
    body = b'{"same": "content"}'
    results: dict[str, str] = {}

    def writer(name: str) -> None:
        try:
            DurableRawMatchStore(directory).store("BR1_888", "match", body, _sha(body))
            results[name] = "SUCCESS"
        except RawStoreCorruptedError:
            results[name] = "REJECTED"

    with mock.patch.object(
        os, "link", side_effect=_patched_link_pausing_before_ref_publish(barrier)
    ):
        t1 = threading.Thread(target=writer, args=("a",))
        t2 = threading.Thread(target=writer, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

    assert results == {"a": "SUCCESS", "b": "SUCCESS"}
    assert DurableRawMatchStore(directory).read_if_valid("BR1_888", "match") == body


def test_reader_during_concurrent_publish_never_observes_partial_state(tmp_path) -> None:
    """Leitor concorrente durante a publicação (revisão CC-201): como o
    conteúdo completo vai para um arquivo temporário antes do hard link
    exclusivo, um leitor nunca pode observar o ponteiro apontando para um
    blob parcialmente escrito — só None (nada publicado ainda) ou o
    conteúdo íntegro publicado."""
    directory = tmp_path / "raw"
    body = b'{"full": "content-never-partial"}'
    writer_may_link = threading.Event()
    original_link = os.link
    observed_during_publish: list[bytes | None] = []

    def paused_link(src, dst, *args, **kwargs):
        if str(dst).endswith(".ref"):
            writer_may_link.wait(timeout=5)
        return original_link(src, dst, *args, **kwargs)

    def reader() -> None:
        for _ in range(200):
            observed_during_publish.append(
                DurableRawMatchStore(directory).read_if_valid("BR1_321", "match")
            )

    with mock.patch.object(os, "link", side_effect=paused_link):
        writer_thread = threading.Thread(
            target=lambda: DurableRawMatchStore(directory).store(
                "BR1_321", "match", body, _sha(body)
            )
        )
        reader_thread = threading.Thread(target=reader)
        writer_thread.start()
        reader_thread.start()
        writer_may_link.set()
        writer_thread.join(timeout=5)
        reader_thread.join(timeout=5)

    assert all(observed in (None, body) for observed in observed_during_publish)
    assert DurableRawMatchStore(directory).read_if_valid("BR1_321", "match") == body
