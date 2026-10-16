"""Filtros explícitos da coleta (CC-206), avaliados no documento da PARTIDA antes de
gastar a requisição da timeline. Cada perda tem um motivo próprio e é contada; uma
partida excluída nunca grava corpo algum. Um motivo por partida: o PRIMEIRO filtro
que falha, na ordem abaixo (a contagem por filtro é de "primeira falha")."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from riftlab.ingestion.infrastructure.riot_source import _COMPLETED_RESULT, _patch_from_game_version
from riftlab.shared.errors import SourceSchemaError

_BR1_MATCH_ID = re.compile(r"^BR1_[0-9]{1,20}$")


class FilterReason(StrEnum):
    PLATFORM_OUT_OF_SCOPE = "PLATFORM_OUT_OF_SCOPE"
    MATCH_DOCUMENT_INVALID = "MATCH_DOCUMENT_INVALID"
    QUEUE_OUT_OF_SCOPE = "QUEUE_OUT_OF_SCOPE"
    MAP_OUT_OF_SCOPE = "MAP_OUT_OF_SCOPE"
    NOT_GAME_COMPLETE = "NOT_GAME_COMPLETE"
    PATCH_OUT_OF_SCOPE = "PATCH_OUT_OF_SCOPE"
    SEED_NOT_IN_MATCH = "SEED_NOT_IN_MATCH"


@dataclass(frozen=True, slots=True)
class MatchFilterConfig:
    """Recorte da coleta: vem de `configs/scope.yaml` (nunca alarga o recorte)."""

    queues: frozenset[int]
    map_id: int
    patches: frozenset[str]
    scope_version: str | None = None


# Filas ranqueadas de Summoner's Rift: Solo/Duo (420) e Flex (440). A listagem da
# Riot aceita uma única `queue`; para as duas, pede `type=ranked` e o filtro do
# documento continua decidindo a fila exata.
RANKED_QUEUES = frozenset({420, 440})


def listing_filter(queues: frozenset[int]) -> tuple[int | None, str | None]:
    """(`queue`, `type`) da listagem por conta: o menor superconjunto expressável."""
    if len(queues) == 1:
        return next(iter(queues)), None
    if queues <= RANKED_QUEUES:
        return None, "ranked"
    return None, None


@dataclass(frozen=True, slots=True)
class FilterResult:
    reason: FilterReason | None
    patch: str | None = None
    # AAAA-MM-DD (UTC) do início da partida, quando o documento a traz: serve para
    # conferir a janela de datas e calibrar o corte por patch; não é dado pessoal.
    game_date: str | None = None

    @property
    def accepted(self) -> bool:
        return self.reason is None


def evaluate_match_id(match_id: str) -> FilterReason | None:
    """Antes de qualquer requisição: a listagem regional (Americas) pode trazer partidas de
    outra plataforma (ex.: NA1_...); só BR1 está no recorte. Custo zero."""
    return None if _BR1_MATCH_ID.fullmatch(match_id) else FilterReason.PLATFORM_OUT_OF_SCOPE


def evaluate_match_document(
    match_body: bytes, config: MatchFilterConfig, seed_puuid: str
) -> FilterResult:
    try:
        document: Any = json.loads(match_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return FilterResult(FilterReason.MATCH_DOCUMENT_INVALID)
    info = document.get("info") if isinstance(document, dict) else None
    metadata = document.get("metadata") if isinstance(document, dict) else None
    if not isinstance(info, dict) or not isinstance(metadata, dict):
        return FilterResult(FilterReason.MATCH_DOCUMENT_INVALID)

    game_date = _game_date(info.get("gameStartTimestamp"))
    if info.get("queueId") not in config.queues:
        return FilterResult(FilterReason.QUEUE_OUT_OF_SCOPE, game_date=game_date)
    if info.get("mapId") != config.map_id:
        return FilterResult(FilterReason.MAP_OUT_OF_SCOPE, game_date=game_date)
    if info.get("endOfGameResult") != _COMPLETED_RESULT:
        return FilterResult(FilterReason.NOT_GAME_COMPLETE, game_date=game_date)
    game_version = info.get("gameVersion")
    if not isinstance(game_version, str):
        return FilterResult(FilterReason.MATCH_DOCUMENT_INVALID, game_date=game_date)
    try:
        patch = _patch_from_game_version(game_version)
    except SourceSchemaError:
        return FilterResult(FilterReason.MATCH_DOCUMENT_INVALID, game_date=game_date)
    if patch not in config.patches:
        return FilterResult(FilterReason.PATCH_OUT_OF_SCOPE, game_date=game_date)
    participants = metadata.get("participants")
    if not isinstance(participants, list) or seed_puuid not in participants:
        return FilterResult(FilterReason.SEED_NOT_IN_MATCH, game_date=game_date)
    return FilterResult(None, patch, game_date)


def _game_date(timestamp_ms: Any) -> str | None:
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms < 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None
