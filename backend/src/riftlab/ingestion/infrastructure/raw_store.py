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
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_KINDS = ("match", "timeline")


class RawStoreCorruptedError(Exception):
    """Estado em disco não pode ser confiado sem resolução manual — nunca
    sobrescrito automaticamente (mesma postura de RawMatchStorage do CC-105,
    reimplementada aqui para acesso durável do worker CC-201, não o
    manifesto JSON do spike). Cobre três causas, sempre com a causa real na
    mensagem (revisão CC-201): (1) um blob referenciado pelo ponteiro não
    existe mais em disco; (2) o conteúdo de um blob diverge do próprio nome
    (hash do conteúdo != nome do arquivo); (3) conflito de publicação —
    dois conteúdos diferentes reivindicam a mesma identidade (match_id,
    kind), ex.: um worker que perdeu o lease ainda termina de escrever."""


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Escreve num arquivo temporário no mesmo diretório e usa os.replace
    (atômico em POSIX, mesma partição): o arquivo final nunca fica visível
    parcialmente escrito, mesmo se o processo morrer no meio. os.replace
    SEMPRE "vence" (substitui o que já estiver lá) — nunca usar isto para
    publicar uma identidade write-once (ver _publish_once abaixo; revisão
    CC-201, achado 1: dois escritores concorrentes que checam
    `not path.exists()` antes de chamar isto podem os dois observar ausência
    e os dois "ganharem", um sobrescrevendo o outro silenciosamente)."""
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    tmp_path.write_bytes(data)
    os.replace(tmp_path, path)


def _publish_once(path: Path, data: bytes) -> bool:
    """Publica `path` com `data` de forma write-once e exclusiva de verdade:
    o conteúdo completo vai primeiro para um arquivo temporário no mesmo
    diretório (mesmo filesystem), depois um hard link exclusivo
    (os.link, falha com FileExistsError se o destino já existir) o publica —
    nunca um `if path.exists()` seguido de escrita, que é check-then-act e
    permite dois escritores concorrentes observarem ausência e os dois
    "vencerem" (revisão CC-201, achado 1, reproduzido com barreira real:
    os.replace sempre substitui, então não é suficiente aqui). Um leitor
    nunca observa `path` parcialmente escrito: o inode só passa a ter esse
    nome depois de já conter o conteúdo inteiro. Retorna True se este
    chamador publicou de fato; False se outro já publicou ali primeiro (o
    chamador decide o que fazer com o conteúdo já existente)."""
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    tmp_path.write_bytes(data)
    try:
        os.link(tmp_path, path)
        return True
    except FileExistsError:
        return False
    finally:
        tmp_path.unlink(missing_ok=True)


class DurableRawMatchStore:
    """Armazenamento durável de corpos brutos match+timeline para o
    importador real (CC-201) — distinto do manifesto JSON do spike CC-105
    (RawMatchStorage), que não é seguro para acesso concorrente/durável e
    nunca é alterado por este módulo. directory deve ser um volume durável
    (ver Settings.raw_storage_dir).

    Publicação imutável endereçada por conteúdo (revisão CC-201: publicar
    corpo+hash como uma unidade só, nunca dois os.replace separados que
    podem intercalar com outro escritor): o corpo vai para
    blobs/<sha256>.json, gravado atomicamente e nunca reescrito depois de
    existir (mesmo conteúdo = mesmo nome, por definição idempotente); um
    ponteiro pequeno <match_id>.<kind>.ref (só o hash, texto) é a única
    coisa que muda de estado, também gravado atomicamente — reduz "publicar
    duas partes como uma unidade" a "atualizar atomicamente um ponteiro que
    só aponta para um blob já gravado por completo". Um segundo escritor
    (ex.: dono antigo do lease que ainda não percebeu que perdeu a posse)
    nunca sobrescreve silenciosamente: se o ponteiro já aponta para um hash
    diferente do que ele tenta publicar, a escrita é rejeitada
    (RawStoreCorruptedError) — a identidade (match_id, kind) é
    write-once-do-conteúdo-que-vencer-primeiro, nunca last-write-wins."""

    def __init__(self, directory: Path, *, create: bool = True) -> None:
        self._directory = directory
        self._blobs_directory = directory / "blobs"
        if create:
            self._blobs_directory.mkdir(parents=True, exist_ok=True)

    def read_if_valid_read_only(self, match_id: str, kind: str) -> bytes | None:
        """Backfill: valida ponteiro/blob atual sem migrar legado nem criar diretório.

        Também valida o par legado corpo+sidecar sem migrá-lo; backfill nunca
        escreve no raw, inclusive se o formato antigo estiver presente.
        """
        self._validate(match_id, kind)
        ref_path = self._ref_path(match_id, kind)
        if not ref_path.exists():
            legacy_body = self._directory / f"{match_id}.{kind}.json"
            legacy_hash = self._directory / f"{match_id}.{kind}.sha256"
            if not legacy_body.exists() and not legacy_hash.exists():
                return None
            if not legacy_body.exists() or not legacy_hash.exists():
                raise RawStoreCorruptedError("raw legado incompleto")
            body = legacy_body.read_bytes()
            expected = legacy_hash.read_text().strip()
            if (
                not _SHA256_HEX_PATTERN.fullmatch(expected)
                or hashlib.sha256(body).hexdigest() != expected
            ):
                raise RawStoreCorruptedError("hash do raw legado divergente")
            return body
        referenced_hash = ref_path.read_text().strip()
        if not _SHA256_HEX_PATTERN.fullmatch(referenced_hash):
            raise RawStoreCorruptedError("ponteiro de raw inválido")
        return self._read_blob(match_id, kind, referenced_hash)

    def read_if_valid(self, match_id: str, kind: str) -> bytes | None:
        """None se nunca foi gravado (nem no formato atual, nem no legado
        pré-correção). Bytes se presente e íntegro. Levanta
        RawStoreCorruptedError se o estado em disco não pode ser confiado —
        nunca finge ausência nem sobrescreve sozinho."""
        self._validate(match_id, kind)
        ref_path = self._ref_path(match_id, kind)
        if not ref_path.exists():
            return self._migrate_legacy_if_present(match_id, kind)
        referenced_hash = ref_path.read_text().strip()
        if not _SHA256_HEX_PATTERN.fullmatch(referenced_hash):
            raise RawStoreCorruptedError(
                f"ponteiro de {kind} para {match_id!r} não contém um hash sha256 "
                "válido; resolução manual exigida"
            )
        return self._read_blob(match_id, kind, referenced_hash)

    def store(self, match_id: str, kind: str, body: bytes, sha256: str) -> None:
        """Só chamado com bytes recém-buscados e já verificados pelo
        transporte (RawRiotResponse.sha256). Idempotente para o mesmo
        conteúdo (inclusive entre execuções/processos diferentes, inclusive
        sob concorrência real — publicação write-once via _publish_once, não
        check-then-act); rejeita publicar um conteúdo diferente sob a mesma
        identidade (match_id, kind) em vez de sobrescrever — ver docstring
        da classe."""
        self._validate(match_id, kind)
        actual_hash = hashlib.sha256(body).hexdigest()
        if actual_hash != sha256:
            raise ValueError("sha256 informado não bate com o corpo fornecido")

        blob_path = self._blob_path(actual_hash)
        if not _publish_once(blob_path, body):
            # Outro escritor (talvez este mesmo processo numa tentativa
            # anterior) já publicou este blob primeiro. Endereçado por
            # conteúdo: mesmo hash só pode significar mesmo conteúdo, mas
            # confirmamos mesmo assim (bit rot/adulteração externa é uma
            # causa real de corrupção).
            if hashlib.sha256(blob_path.read_bytes()).hexdigest() != actual_hash:
                raise RawStoreCorruptedError(
                    f"blob {actual_hash} de {kind} para {match_id!r} diverge do "
                    "próprio nome; resolução manual exigida"
                )

        ref_path = self._ref_path(match_id, kind)
        if _publish_once(ref_path, actual_hash.encode("ascii")):
            return  # este chamador publicou a referência primeiro
        # Outro escritor publicou a referência primeiro nesta identidade —
        # write-once de verdade (hard link exclusivo), não "o último
        # os.replace vence": um segundo escritor concorrente nunca sobrepõe
        # o que o primeiro já publicou, mesmo que ambos tenham observado
        # `not ref_path.exists()` no mesmo instante (revisão CC-201, achado
        # 1 — reproduzido com barreira real antes desta correção).
        existing_hash = ref_path.read_text().strip()
        if existing_hash == actual_hash:
            return  # idempotente: mesma identidade, mesmo conteúdo já publicado
        raise RawStoreCorruptedError(
            f"{kind} de {match_id!r} já publicado com hash {existing_hash} "
            f"diferente do agora recebido ({actual_hash}); nunca sobrescrito "
            "automaticamente, resolução manual exigida"
        )

    def _migrate_legacy_if_present(self, match_id: str, kind: str) -> bytes | None:
        """Formato anterior à correção CC-201 (commit 63f638a): corpo em
        `<match_id>.<kind>.json` + sidecar `<match_id>.<kind>.sha256`, direto
        no diretório raiz (sem `blobs/`, sem endereçamento por conteúdo).
        Um diretório com esses arquivos nunca é tratado como "nunca
        coletado" e silenciosamente substituído por nova coleta de rede
        (revisão CC-201, achado 1): migrado para o esquema atual de forma
        não destrutiva (arquivos antigos preservados, nunca apagados) na
        primeira leitura, então servido como se já estivesse no formato
        novo. Ambos ausentes é o único caso de "nunca gravado" — exatamente
        um presente (par legado incompleto, ex.: crash no meio da escrita
        antiga) nunca é confundido com ausência (revisão complementar
        CC-201-REVIEW-3: `_migrate_legacy_if_present` original fazia
        `not body.exists() or not hash.exists(): return None`, tratando
        metade de um par legado como cache vazio e liberando nova coleta em
        cima de dado local já parcialmente presente)."""
        legacy_body_path = self._directory / f"{match_id}.{kind}.json"
        legacy_hash_path = self._directory / f"{match_id}.{kind}.sha256"
        body_exists = legacy_body_path.exists()
        hash_exists = legacy_hash_path.exists()
        if not body_exists and not hash_exists:
            return None
        if body_exists != hash_exists:
            raise RawStoreCorruptedError(
                f"{kind} de {match_id!r} em formato legado incompleto (só "
                f"{'corpo' if body_exists else 'sidecar .sha256'} presente); "
                "resolução manual exigida, sem migração automática"
            )
        body = legacy_body_path.read_bytes()
        expected_hash = legacy_hash_path.read_text().strip()
        actual_hash = hashlib.sha256(body).hexdigest()
        if not _SHA256_HEX_PATTERN.fullmatch(expected_hash) or actual_hash != expected_hash:
            raise RawStoreCorruptedError(
                f"{kind} de {match_id!r} em formato legado diverge do hash do "
                "sidecar .sha256; resolução manual exigida, sem migração automática"
            )
        self.store(match_id, kind, body, actual_hash)
        return body

    def _read_blob(self, match_id: str, kind: str, expected_hash: str) -> bytes:
        blob_path = self._blob_path(expected_hash)
        if not blob_path.exists():
            raise RawStoreCorruptedError(
                f"ponteiro de {kind} para {match_id!r} referencia o blob "
                f"{expected_hash}, que não existe mais em disco; resolução "
                "manual exigida"
            )
        body = blob_path.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected_hash:
            raise RawStoreCorruptedError(
                f"blob {expected_hash} de {kind} para {match_id!r} diverge do "
                "próprio nome; resolução manual exigida"
            )
        return body

    def _validate(self, match_id: str, kind: str) -> None:
        if not _SAFE_MATCH_ID_PATTERN.fullmatch(match_id):
            raise ValueError(f"match_id fora do formato esperado: {match_id!r}")
        if kind not in _KINDS:
            raise ValueError(f"kind deve ser 'match' ou 'timeline', recebido {kind!r}")

    def _ref_path(self, match_id: str, kind: str) -> Path:
        return self._directory / f"{match_id}.{kind}.ref"

    def _blob_path(self, content_hash: str) -> Path:
        return self._blobs_directory / f"{content_hash}.json"
