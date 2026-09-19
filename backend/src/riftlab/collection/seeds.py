"""Lista permitida de sementes da coleta (CC-206).

Uma semente é uma conta com CONSENTIMENTO explícito, dada por um arquivo de conta
privado (o mesmo formato do arquivo de conta de `cc riot audit-sample`). O arquivo de sementes só
referencia esses arquivos por caminho e dá a cada um um RÓTULO pseudônimo estável
(`seed-01`): é o rótulo, nunca o Riot ID, que aparece em manifesto, relatório e log.

Rejeita, de propósito: consentimento ausente ou diferente de `true`, chave
desconhecida (nenhum nome, tag ou PUUID inline), rótulo repetido ou fora do padrão, e
conta fora do recorte BR1/Americas. Nunca lê participantes de partidas para criar
sementes novas — a lista permitida é a única origem."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from riftlab.ingestion.infrastructure.riot_account import (
    RiotAccountConfig,
    RiotAccountConfigError,
    load_riot_account,
)

SEEDS_SCHEMA_VERSION = "v1"
MAX_SEEDS = 20
_LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_ALLOWED_TOP_KEYS = {"schemaVersion", "seeds"}
_ALLOWED_SEED_KEYS = {"label", "accountFile", "consent"}


class SeedsFileError(Exception):
    """Arquivo de sementes inválido. As mensagens citam rótulos e nomes de campo, nunca
    o conteúdo de um arquivo de conta."""


@dataclass(frozen=True, slots=True)
class Seed:
    label: str
    account: RiotAccountConfig

    def __repr__(self) -> str:  # nunca vaza o Riot ID em traceback/log
        return f"Seed(label={self.label!r})"


def load_seeds(path: Path) -> list[Seed]:
    if not path.is_file():
        raise SeedsFileError(f"arquivo de sementes não encontrado: {path}")
    try:
        document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise SeedsFileError("arquivo de sementes não é YAML/UTF-8 válido") from exc
    if not isinstance(document, dict):
        raise SeedsFileError("raiz do arquivo de sementes deve ser um objeto")
    unknown = sorted(set(document) - _ALLOWED_TOP_KEYS)
    if unknown:
        raise SeedsFileError(f"chave(s) desconhecida(s) na raiz: {', '.join(map(str, unknown))}")
    if document.get("schemaVersion") != SEEDS_SCHEMA_VERSION:
        raise SeedsFileError(f"schemaVersion deve ser {SEEDS_SCHEMA_VERSION!r}")
    entries = document.get("seeds")
    if not isinstance(entries, list) or not entries:
        raise SeedsFileError("seeds deve ser uma lista não vazia")
    if len(entries) > MAX_SEEDS:
        raise SeedsFileError(f"no máximo {MAX_SEEDS} sementes por arquivo")

    seeds: list[Seed] = []
    seen_labels: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        seeds.append(_load_entry(index, entry, path.parent, seen_labels))
    return seeds


def _load_entry(index: int, entry: Any, base: Path, seen_labels: set[str]) -> Seed:
    if not isinstance(entry, dict):
        raise SeedsFileError(f"semente #{index} deve ser um objeto")
    label = entry.get("label")
    if not isinstance(label, str) or not _LABEL_PATTERN.fullmatch(label):
        raise SeedsFileError(f"semente #{index}: label deve casar {_LABEL_PATTERN.pattern}")
    if label in seen_labels:
        raise SeedsFileError(f"semente {label!r}: label repetido")
    seen_labels.add(label)
    unknown = sorted(set(entry) - _ALLOWED_SEED_KEYS)
    if unknown:
        raise SeedsFileError(
            f"semente {label!r}: chave(s) desconhecida(s) {', '.join(map(str, unknown))} "
            "(nenhum dado de conta pode ficar no arquivo de sementes)"
        )
    if entry.get("consent") is not True:
        raise SeedsFileError(
            f"semente {label!r}: consent deve ser true (consentimento explícito da pessoa)"
        )
    account_file = entry.get("accountFile")
    if not isinstance(account_file, str) or not account_file.strip():
        raise SeedsFileError(f"semente {label!r}: accountFile deve ser um caminho")
    resolved = Path(account_file)
    if not resolved.is_absolute():
        resolved = base / resolved
    try:
        account = load_riot_account(resolved)
    except RiotAccountConfigError as exc:
        raise SeedsFileError(f"semente {label!r}: conta inválida ({exc})") from exc
    return Seed(label=label, account=account)
