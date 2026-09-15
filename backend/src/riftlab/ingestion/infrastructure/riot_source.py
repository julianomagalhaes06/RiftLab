"""Adapter Riot para MatchSource (CC-201): Match-V5 + Timeline-V5 reais, nunca
Live Client Data/Spectator (RF17 — só existe consulta a partida concluída).
Reaproveita RiotHttpTransport (CC-105) para HTTP/classificação de erro/host
fechado/chave só em header, mas configurado com max_attempts_per_request=1:
o job em import_jobs é o único dono do retry transitório (docs/architecture.md,
plano do CC-201) — o transporte nunca dorme/retenta sozinho aqui, só reporta
o erro real imediatamente para o worker decidir RETRY_WAIT (durável) ou
FAILED (permanente)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from riftlab.ingestion.domain import (
    MatchParticipantSourceDTO,
    MatchSourceResult,
    TimelineEventDTO,
    TimelineFrameDTO,
)
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore, RawStoreCorruptedError
from riftlab.ingestion.infrastructure.riot_transport import RiotHttpTransport
from riftlab.ingestion.infrastructure.subject_hash import compute_real_subject_hash
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import SourceError, SourceSchemaError

# endOfGameResult real (auditado em CC-105, data-audit.md): só este valor
# confirma conclusão normal. Qualquer outro (remake/abandono/ausente) não é
# importável — "somente partidas concluídas conforme campos auditados"
# (critério adicional do card).
_COMPLETED_RESULT = "GameComplete"

_REQUIRED_MATCH_INFO_FIELDS = (
    "queueId",
    "mapId",
    "gameVersion",
    "gameStartTimestamp",
    "gameDuration",
    "participants",
    "endOfGameResult",
)


class RiotMatchSource:
    """MatchSource real: busca (ou reaproveita do armazenamento durável) o par
    match+timeline, persiste os corpos brutos e converte para os DTOs
    compartilhados com a fonte demo. subject_hash_secret é obrigatório aqui —
    nunca um valor default/fixo (ver subject_hash.compute_real_subject_hash)."""

    def __init__(
        self,
        transport: RiotHttpTransport,
        raw_store: DurableRawMatchStore,
        subject_hash_secret: str | None,
    ) -> None:
        self._transport = transport
        self._raw_store = raw_store
        self._subject_hash_secret = subject_hash_secret

    async def load_match(self, match_id: str) -> MatchSourceResult:
        match_bytes = await self._obtain(match_id, "match")
        timeline_bytes = await self._obtain(match_id, "timeline")
        return _parse_riot_match_and_timeline(
            match_id, match_bytes, timeline_bytes, self._subject_hash_secret
        )

    async def _obtain(self, match_id: str, kind: str) -> bytes:
        try:
            cached = self._raw_store.read_if_valid(match_id, kind)
        except RawStoreCorruptedError as exc:
            raise SourceError(
                code="RAW_STORE_CORRUPTED",
                detail=f"dado bruto local de {kind} corrompido; resolução manual exigida",
            ) from exc
        if cached is not None:
            return cached

        fetch = self._transport.fetch_match if kind == "match" else self._transport.fetch_timeline
        response = await fetch(match_id)
        self._raw_store.store(match_id, kind, response.body, response.sha256)
        return response.body


def _parse_json(kind: str, body: bytes) -> Any:
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SourceSchemaError(
            code="SCHEMA_UNSUPPORTED", detail=f"corpo de {kind} não é JSON válido"
        ) from exc


def _patch_from_game_version(game_version: str) -> str:
    """ "13.10.500.1234" -> "13.10" (major.minor, mesmo estilo dos patches em
    configs/scope.yaml). gameVersion vazio/sem dois segmentos é schema
    incompatível, nunca um patch inventado."""
    parts = game_version.split(".")
    if len(parts) < 2 or not all(parts[0:2]):
        raise SourceSchemaError(
            code="SCHEMA_UNSUPPORTED", detail=f"gameVersion não reconhecida: {game_version!r}"
        )
    return f"{parts[0]}.{parts[1]}"


def _parse_riot_match_and_timeline(
    match_id: str,
    match_body: bytes,
    timeline_body: bytes,
    subject_hash_secret: str | None,
) -> MatchSourceResult:
    match_doc = _parse_json("match", match_body)
    timeline_doc = _parse_json("timeline", timeline_body)
    if not isinstance(match_doc, dict) or not isinstance(timeline_doc, dict):
        raise SourceSchemaError(
            code="SCHEMA_UNSUPPORTED", detail="documento raiz não é objeto JSON"
        )

    match_info = match_doc.get("info")
    timeline_info = timeline_doc.get("info")
    if not isinstance(match_info, dict) or not isinstance(timeline_info, dict):
        raise SourceSchemaError(
            code="MATCH_SOURCE_MISSING_FIELD", detail="campo estrutural 'info' ausente"
        )

    missing = sorted(field for field in _REQUIRED_MATCH_INFO_FIELDS if field not in match_info)
    if missing:
        raise SourceSchemaError(
            code="MATCH_SOURCE_MISSING_FIELD",
            detail=f"campo estrutural obrigatório ausente: {', '.join(missing)}",
        )

    match_document_id = match_doc.get("metadata", {}).get("matchId")
    timeline_document_id = timeline_doc.get("metadata", {}).get("matchId")
    if match_document_id != match_id or timeline_document_id != match_id:
        raise SourceSchemaError(
            code="MATCH_SOURCE_IDENTITY_MISMATCH",
            detail="matchId do corpo diverge do identificador solicitado",
        )

    if match_info.get("endOfGameResult") != _COMPLETED_RESULT:
        raise SourceSchemaError(
            code="MATCH_NOT_COMPLETED",
            detail="partida sem endOfGameResult=GameComplete não é importável (RF17)",
        )

    raw_participants = match_info["participants"]
    if not isinstance(raw_participants, list) or not raw_participants:
        raise SourceSchemaError(
            code="MATCH_SOURCE_MISSING_FIELD", detail="participants vazio ou ausente"
        )

    participants: list[MatchParticipantSourceDTO] = []
    match_participant_ids: set[int] = set()
    for raw_participant in raw_participants:
        participant_id = raw_participant["participantId"]
        puuid = raw_participant.get("puuid")
        if not isinstance(puuid, str) or not puuid:
            raise SourceSchemaError(
                code="MATCH_SOURCE_MISSING_FIELD", detail="participante sem puuid"
            )
        role = raw_participant.get("teamPosition") or raw_participant.get("individualPosition")
        participants.append(
            MatchParticipantSourceDTO(
                participant_id=participant_id,
                champion_id=raw_participant["championId"],
                team=raw_participant["teamId"],
                subject_hash=compute_real_subject_hash(puuid, subject_hash_secret),
                role=role or None,
            )
        )
        match_participant_ids.add(participant_id)

    timeline_participant_ids = {
        p["participantId"] for p in timeline_info.get("participants", []) if "participantId" in p
    }
    if timeline_participant_ids and timeline_participant_ids != match_participant_ids:
        raise SourceSchemaError(
            code="MATCH_TIMELINE_IDENTITY_MISMATCH",
            detail="participantes do timeline divergem dos participantes da partida",
        )

    raw_frames = timeline_info.get("frames", [])
    frames: list[TimelineFrameDTO] = []
    events: list[TimelineEventDTO] = []
    source_index = 0
    for raw_frame in raw_frames:
        frames.append(
            TimelineFrameDTO(
                timestamp_ms=raw_frame["timestamp"],
                participant_frames={
                    int(pid): dict(pf) for pid, pf in raw_frame.get("participantFrames", {}).items()
                },
            )
        )
        for raw_event in raw_frame.get("events", []):
            events.append(
                TimelineEventDTO(
                    source_index=source_index,
                    timestamp_ms=raw_event["timestamp"],
                    type=raw_event["type"],
                    payload={k: v for k, v in raw_event.items() if k not in {"timestamp", "type"}},
                )
            )
            source_index += 1

    return MatchSourceResult(
        match_id=match_id,
        origin=SourceOrigin.COMPLETED_MATCH,
        schema_version="riot-match-v5",
        raw_hash=hashlib.sha256(match_body).hexdigest(),
        queue=match_info["queueId"],
        map_id=match_info["mapId"],
        patch=_patch_from_game_version(match_info["gameVersion"]),
        start_at=datetime.fromtimestamp(match_info["gameStartTimestamp"] / 1000, tz=UTC),
        duration_seconds=match_info["gameDuration"],
        participants=tuple(participants),
        frames=tuple(frames),
        events=tuple(events),
    )
