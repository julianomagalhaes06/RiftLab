"""RawMatchStorage (CC-105): persistência local do spike, hash/retomada sem
sobrescrita silenciosa. Sem rede, sem Postgres — arquivos reais em tmp_path."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from riftlab.ingestion.infrastructure.raw_storage import (
    DEFAULT_OUT_DIR,
    RawMatchStorage,
    RawStorageIntegrityError,
)
from riftlab.ingestion.infrastructure.riot_transport import RawRiotResponse


def test_default_out_dir_is_absolute_and_anchored_not_cwd() -> None:
    # Bug real: um default relativo gravaria dados reais fora do escopo do
    # .gitignore da raiz (/data/, ancorado) caso `cc` rodasse a partir de
    # backend/. O default precisa ser absoluto, terminar em data/raw/cc-105 e
    # nunca ficar aninhado dentro do próprio pacote backend/ (verificado assim,
    # não comparando contra um layout de diretório específico, para continuar
    # válido tanto em dev local — backend/src/riftlab/... — quanto na imagem
    # Docker — /app/src/riftlab/..., sem o nível backend/).
    assert DEFAULT_OUT_DIR.is_absolute()
    assert DEFAULT_OUT_DIR.parts[-3:] == ("data", "raw", "cc-105")
    assert "backend" not in DEFAULT_OUT_DIR.parts


def _response(match_id: str, kind: str, body: bytes) -> RawRiotResponse:
    return RawRiotResponse(
        match_id=match_id,
        kind=kind,
        body=body,
        sha256=hashlib.sha256(body).hexdigest(),
        fetched_at=datetime.now(UTC),
    )


def test_status_missing_when_never_stored(tmp_path) -> None:
    storage = RawMatchStorage(tmp_path)
    assert storage.status("BR1_1000000001") == "missing"


def test_store_then_status_valid_and_resumable_across_instances(tmp_path) -> None:
    storage = RawMatchStorage(tmp_path)
    match = _response("BR1_1000000001", "match", b'{"a":1}')
    timeline = _response("BR1_1000000001", "timeline", b'{"b":2}')

    storage.store(match, timeline)

    assert storage.status("BR1_1000000001") == "valid"
    # nova instancia simula uma execucao futura lendo o manifesto do disco
    reopened = RawMatchStorage(tmp_path)
    assert reopened.status("BR1_1000000001") == "valid"


def test_store_rejects_match_and_timeline_with_different_match_ids(tmp_path) -> None:
    storage = RawMatchStorage(tmp_path)
    match = _response("BR1_1000000001", "match", b"{}")
    timeline = _response("BR1_2000000002", "timeline", b"{}")
    with pytest.raises(ValueError):
        storage.store(match, timeline)


def test_store_rejects_swapped_kinds(tmp_path) -> None:
    storage = RawMatchStorage(tmp_path)
    match = _response("BR1_1000000001", "timeline", b"{}")
    timeline = _response("BR1_1000000001", "match", b"{}")
    with pytest.raises(ValueError):
        storage.store(match, timeline)


def test_corrupted_file_is_detected_and_never_silently_overwritten(tmp_path) -> None:
    storage = RawMatchStorage(tmp_path)
    match = _response("BR1_1000000001", "match", b'{"a":1}')
    timeline = _response("BR1_1000000001", "timeline", b'{"b":2}')
    storage.store(match, timeline)

    (tmp_path / "BR1_1000000001.match.json").write_bytes(b'{"tampered": true}')
    assert storage.status("BR1_1000000001") == "corrupted"

    with pytest.raises(RawStorageIntegrityError):
        storage.store(match, timeline)


def test_storing_one_match_does_not_affect_another(tmp_path) -> None:
    storage = RawMatchStorage(tmp_path)
    storage.store(
        _response("BR1_1000000001", "match", b"1"), _response("BR1_1000000001", "timeline", b"1")
    )
    storage.store(
        _response("BR1_2000000002", "match", b"2"), _response("BR1_2000000002", "timeline", b"2")
    )

    assert storage.status("BR1_1000000001") == "valid"
    assert storage.status("BR1_2000000002") == "valid"


def test_record_exclusion_persists_reason_across_instances(tmp_path) -> None:
    storage = RawMatchStorage(tmp_path)
    storage.record_exclusion("BR1_4040000004", "RIOT_NOT_FOUND")

    reopened = RawMatchStorage(tmp_path)
    assert reopened.excluded_reason("BR1_4040000004") == "RIOT_NOT_FOUND"
    # excluido nunca teve corpo gravado: status continua "missing", nao "valid"
    assert reopened.status("BR1_4040000004") == "missing"


def test_excluded_reason_is_none_for_unknown_match_id(tmp_path) -> None:
    storage = RawMatchStorage(tmp_path)
    assert storage.excluded_reason("BR1_9999999999") is None
