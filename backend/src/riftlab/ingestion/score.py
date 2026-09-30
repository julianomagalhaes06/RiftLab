"""Fatos finais de um participante, sem inferência de conta própria.

O mesmo validador serve aos documentos DEMO e Match-V5. Ausência/null fica
desconhecida; tipo ou faixa inválidos rejeitam a fonte, nunca viram zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from riftlab.shared.errors import SourceSchemaError

MAX_SCORE_COUNT = 2_147_483_647  # INTEGER PostgreSQL


@dataclass(frozen=True, slots=True)
class ParticipantScore:
    win: bool | None = None
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    total_minions_killed: int | None = None
    neutral_minions_killed: int | None = None


def parse_participant_score(raw: dict[str, Any]) -> ParticipantScore:
    win = raw.get("win")
    if win is not None and not isinstance(win, bool):
        raise SourceSchemaError(code="MATCH_SCORE_INVALID", detail="win tem tipo inválido")

    def count(field: str) -> int | None:
        value = raw.get(field)
        if value is None:
            return None
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= MAX_SCORE_COUNT
        ):
            raise SourceSchemaError(code="MATCH_SCORE_INVALID", detail=f"{field} inválido")
        return value

    return ParticipantScore(
        win=win,
        kills=count("kills"),
        deaths=count("deaths"),
        assists=count("assists"),
        total_minions_killed=count("totalMinionsKilled"),
        neutral_minions_killed=count("neutralMinionsKilled"),
    )


def scores_from_riot_match(
    match_id: str, raw_body: bytes
) -> dict[int, tuple[int, int, ParticipantScore]]:
    """Só match bruto local, para backfill; valida identidade e conclusão sem PUUID."""
    try:
        document = json.loads(raw_body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SourceSchemaError(
            code="MATCH_SCORE_INVALID", detail="raw de partida inválido"
        ) from exc
    if not isinstance(document, dict) or not isinstance(document.get("metadata"), dict):
        raise SourceSchemaError(code="MATCH_SCORE_INVALID", detail="metadata ausente")
    if document["metadata"].get("matchId") != match_id:
        raise SourceSchemaError(
            code="MATCH_SOURCE_IDENTITY_MISMATCH", detail="raw de outra partida"
        )
    info = document.get("info")
    if not isinstance(info, dict) or info.get("endOfGameResult") != "GameComplete":
        raise SourceSchemaError(code="MATCH_NOT_COMPLETED", detail="partida não concluída")
    participants = info.get("participants")
    if not isinstance(participants, list) or not participants:
        raise SourceSchemaError(code="MATCH_SCORE_INVALID", detail="participantes ausentes")
    result: dict[int, tuple[int, int, ParticipantScore]] = {}
    for raw in participants:
        if not isinstance(raw, dict):
            raise SourceSchemaError(code="MATCH_SCORE_INVALID", detail="participante inválido")
        participant_id = _identity_int(raw.get("participantId"))
        champion_id = _identity_int(raw.get("championId"))
        team = _identity_int(raw.get("teamId"))
        if not 1 <= participant_id <= 10 or champion_id < 1 or team < 0 or participant_id in result:
            raise SourceSchemaError(
                code="MATCH_SCORE_INVALID", detail="identidade de participante inválida"
            )
        result[participant_id] = (champion_id, team, parse_participant_score(raw))
    return result


def _identity_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SourceSchemaError(
            code="MATCH_SCORE_INVALID", detail="identidade de participante inválida"
        )
    return value
