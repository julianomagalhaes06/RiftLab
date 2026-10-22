"""Riot ID dos participantes de uma partida importada (CC-1210), lido na hora da resposta bruta
da Riot que o worker guardou. O banco não guarda Riot ID de terceiros (CC-1103) e esta leitura
não grava nada: a API monta o armazenamento bruto só para leitura. Expurgar a partida apaga o
bruto e, com ele, os nomes. O PUUID que também está no bruto nunca sai daqui.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from pathlib import Path
from typing import Any

from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore, RawStoreCorruptedError
from riftlab.shared.api.common import MATCH_ID_DEMO_PATTERN

logger = logging.getLogger(__name__)
_MAX_PART = 32


def _part(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFC", value).strip()
    if not text or len(text) > _MAX_PART or "#" in text:
        return None
    if any(unicodedata.category(c).startswith("C") for c in text):
        return None
    return text


def participant_riot_ids(raw_dir: str, match_id: str) -> dict[int, tuple[str, str]]:
    """participantId → (Nome, TAG) como estavam na partida. {} para DEMO, bruto ausente,
    corrompido ou sem Riot ID (partidas antigas só com summonerName ficam sem nome)."""
    if MATCH_ID_DEMO_PATTERN.fullmatch(match_id):
        return {}
    try:
        body = DurableRawMatchStore(Path(raw_dir), create=False).read_if_valid_read_only(
            match_id, "match"
        )
    except (RawStoreCorruptedError, OSError):
        logger.warning("bruto da partida ilegível para Riot ID", extra={"matchId": match_id})
        return {}
    if body is None:
        return {}
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        return {}
    info = document.get("info") if isinstance(document, dict) else None
    participants = info.get("participants") if isinstance(info, dict) else None
    found: dict[int, tuple[str, str]] = {}
    for raw in participants if isinstance(participants, list) else []:
        if not isinstance(raw, dict):
            continue
        pid = raw.get("participantId")
        name, tag = _part(raw.get("riotIdGameName")), _part(raw.get("riotIdTagline"))
        if isinstance(pid, int) and not isinstance(pid, bool) and name and tag:
            found[pid] = (name, tag)
    return found
