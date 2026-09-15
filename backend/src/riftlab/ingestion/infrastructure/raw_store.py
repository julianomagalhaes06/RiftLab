from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

# Mesmos formatos aceitos em qualquer camada de borda do projeto
# (shared/api/common.py) — validado aqui de novo como defesa em profundidade:
# este módulo nunca confia que o chamador já validou o match_id.
_SAFE_MATCH_ID_PATTERN = re.compile(r"^(BR1_[0-9]{1,20}|DEMO-\d{4})$")

_KINDS = ("match", "timeline")


class RawStoreCorruptedError(Exception):
    """Bytes em disco divergem do hash registrado ao lado (sidecar
    .sha256) — resolução manual exigida, nunca sobrescrita automática
    silenciosa (mesma postura de RawMatchStorage do CC-105, reimplementada
    aqui para acesso durável do worker CC-201, não o manifesto JSON do
    spike)."""


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Escreve num arquivo temporário no mesmo diretório e usa os.replace
    (atômico em POSIX, mesma partição): o arquivo final nunca fica visível
    parcialmente escrito, mesmo se o processo morrer no meio — elimina a
    classe de corrupção por escrita parcial (diferente do manifesto do
    CC-105, que não tinha essa garantia)."""
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    tmp_path.write_bytes(data)
    os.replace(tmp_path, path)


class DurableRawMatchStore:
    """Armazenamento durável de corpos brutos match+timeline para o
    importador real (CC-201) — distinto do manifesto JSON do spike CC-105
    (RawMatchStorage), que não é seguro para acesso concorrente/durável e
    nunca é alterado por este módulo. directory deve ser um volume durável
    (ver Settings.raw_storage_dir); cada match_id vira dois arquivos
    (<id>.match.json/<id>.timeline.json) mais um sidecar .sha256 cada,
    permitindo a CC-203/204 recuperar os frames brutos sem nova coleta
    (contrato do plano do CC-201)."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def read_if_valid(self, match_id: str, kind: str) -> bytes | None:
        """None se nunca foi gravado. Bytes se presente e íntegro (hash do
        sidecar bate). Levanta RawStoreCorruptedError se presente mas
        divergente — nunca finge que está ausente nem sobrescreve sozinho."""
        self._validate(match_id, kind)
        body_path = self._body_path(match_id, kind)
        hash_path = self._hash_path(match_id, kind)
        if not body_path.exists() or not hash_path.exists():
            return None
        body = body_path.read_bytes()
        expected = hash_path.read_text().strip()
        actual = hashlib.sha256(body).hexdigest()
        if actual != expected:
            raise RawStoreCorruptedError(
                f"{kind} de {match_id!r} em disco diverge do hash esperado; "
                "resolução manual exigida, sem sobrescrita automática"
            )
        return body

    def store(self, match_id: str, kind: str, body: bytes, sha256: str) -> None:
        """Só chamado com bytes recém-buscados e já verificados pelo
        transporte (RawRiotResponse.sha256) — sempre confiável, portanto
        sempre grava (gravação atômica substitui qualquer conteúdo anterior,
        íntegro ou não; a checagem de integridade é para decidir se PULA a
        rede antes de chamar store(), não depois)."""
        self._validate(match_id, kind)
        if hashlib.sha256(body).hexdigest() != sha256:
            raise ValueError("sha256 informado não bate com o corpo fornecido")
        _atomic_write_bytes(self._body_path(match_id, kind), body)
        _atomic_write_bytes(self._hash_path(match_id, kind), sha256.encode("ascii"))

    def _validate(self, match_id: str, kind: str) -> None:
        if not _SAFE_MATCH_ID_PATTERN.fullmatch(match_id):
            raise ValueError(f"match_id fora do formato esperado: {match_id!r}")
        if kind not in _KINDS:
            raise ValueError(f"kind deve ser 'match' ou 'timeline', recebido {kind!r}")

    def _body_path(self, match_id: str, kind: str) -> Path:
        return self._directory / f"{match_id}.{kind}.json"

    def _hash_path(self, match_id: str, kind: str) -> Path:
        return self._directory / f"{match_id}.{kind}.sha256"
