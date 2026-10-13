"""CC-603: expurgo dos brutos fica no armazenamento, é idempotente e não apaga blob alheio."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore


def _store(store: DurableRawMatchStore, match_id: str, body: bytes) -> None:
    for kind in ("match", "timeline"):
        data = body + kind.encode()
        store.store(match_id, kind, data, hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize(
    "match_id", ["../etc/passwd", "BR1_1/../../x", "DEMO-0001/..", "/tmp/x", "BR1_", ""]
)
def test_purge_refuses_ids_outside_the_closed_format(tmp_path: Path, match_id: str) -> None:
    store = DurableRawMatchStore(tmp_path / "raw")
    with pytest.raises(ValueError):
        store.purge(match_id)


def test_purge_removes_refs_blobs_and_legacy_files_and_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    store = DurableRawMatchStore(root)
    _store(store, "BR1_111", b"a")
    _store(store, "BR1_222", b"b")
    (root / "BR1_111.match.json").write_bytes(b"legado")
    (root / "BR1_111.match.sha256").write_text("x")

    assert store.purge("BR1_111") == 6  # 2 refs, 2 blobs, 2 legados
    assert not list(root.glob("BR1_111*"))
    assert store.read_if_valid("BR1_222", "match") == b"bmatch"  # a outra partida intacta
    assert store.purge("BR1_111") == 0  # repetir não faz mal


def test_blob_shared_with_another_match_is_kept(tmp_path: Path) -> None:
    store = DurableRawMatchStore(tmp_path / "raw")
    _store(store, "BR1_111", b"igual")
    _store(store, "BR1_222", b"igual")  # mesmo conteúdo, mesmos blobs

    assert store.purge("BR1_111") == 2  # só os ponteiros
    assert store.read_if_valid("BR1_222", "timeline") == b"igualtimeline"


def test_symlinked_ref_is_removed_as_a_link_and_its_target_is_untouched(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    store = DurableRawMatchStore(root)
    outside = tmp_path / "fora.txt"
    outside.write_text("não pode sumir")
    (root / "BR1_333.match.ref").symlink_to(outside)

    assert store.purge("BR1_333") == 1
    assert outside.read_text() == "não pode sumir"
    assert not (root / "BR1_333.match.ref").exists()
