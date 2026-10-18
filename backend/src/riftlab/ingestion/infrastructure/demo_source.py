from __future__ import annotations

import hashlib
import json
from datetime import datetime

from riftlab.ingestion.domain import (
    MatchParticipantSourceDTO,
    MatchSourceResult,
    TimelineEventDTO,
    TimelineFrameDTO,
)
from riftlab.ingestion.final_items import parse_final_items
from riftlab.ingestion.score import parse_participant_score
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import SourceNotFoundError, SourceSchemaError
from riftlab.shared.resources import read_package_bytes, read_package_json

SUPPORTED_SCHEMA_VERSION = "v1"

_FIXTURES_PACKAGE = "riftlab.ingestion.infrastructure.fixtures"
_MANIFEST_NAME = "manifest.json"

_REQUIRED_MATCH_FIELDS = (
    "queue",
    "mapId",
    "patch",
    "startAt",
    "durationSeconds",
    "participants",
    "frames",
)


def _demo_subject_hash(match_id: str, participant_id: int) -> str:
    """Fixtures demo não têm PUUID real algum — nada a proteger com segredo.
    Hash não-secreto, só para ocupar o mesmo formato de identidade opaca que
    a fonte Riot usa (subject_hash), claramente derivado de dado sintético."""
    return hashlib.sha256(f"DEMO:{match_id}:{participant_id}".encode()).hexdigest()


class DemoMatchSource:
    """MatchSource cujo conteúdo vem inteiramente de fixtures sintéticas
    empacotadas com o backend (mesma imagem runtime e test, sem rede). Só serve
    identificadores presentes no manifesto fechado; qualquer outro — inclusive um
    que pareça uma partida real — é rejeitado, nunca atendido por aproximação ou
    fallback silencioso."""

    def __init__(self) -> None:
        manifest = read_package_json(_FIXTURES_PACKAGE, _MANIFEST_NAME)
        self._scenarios: dict[str, str] = {
            match_id: entry["file"] for match_id, entry in manifest["scenarios"].items()
        }

    async def load_match(self, match_id: str) -> MatchSourceResult:
        relative_path = self._scenarios.get(match_id)
        if relative_path is None:
            raise SourceNotFoundError(
                code="MATCH_SOURCE_NOT_FOUND",
                detail=f"identificador demo desconhecido: {match_id!r}",
            )
        raw_bytes = read_package_bytes(_FIXTURES_PACKAGE, relative_path)
        return parse_match_source_document(match_id, raw_bytes)


def parse_match_source_document(match_id: str, raw_bytes: bytes) -> MatchSourceResult:
    """Pura: separa leitura/validação do JSON bruto da conversão para DTOs. Usada
    tanto pelo adapter demo quanto pelos testes de payload inválido/faltante, sem
    precisar empacotar arquivos deliberadamente quebrados."""
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()

    try:
        document = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise SourceSchemaError(
            code="MATCH_SOURCE_INVALID_JSON", detail="corpo da fonte não é JSON válido"
        ) from exc

    schema_version = document.get("schemaVersion")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise SourceSchemaError(
            code="SCHEMA_UNSUPPORTED",
            detail=f"schemaVersion não suportada pela fonte demo: {schema_version!r}",
        )

    document_match_id = document.get("matchId")
    if document_match_id is not None and document_match_id != match_id:
        raise SourceSchemaError(
            code="MATCH_SOURCE_IDENTITY_MISMATCH",
            detail="matchId do documento diverge do identificador solicitado ao manifesto",
        )

    missing = sorted(field for field in _REQUIRED_MATCH_FIELDS if field not in document)
    if missing:
        raise SourceSchemaError(
            code="MATCH_SOURCE_MISSING_FIELD",
            detail=f"campo estrutural obrigatório ausente: {', '.join(missing)}",
        )

    participants = tuple(
        MatchParticipantSourceDTO(
            participant_id=raw_participant["participantId"],
            champion_id=raw_participant["championId"],
            team=raw_participant["team"],
            subject_hash=_demo_subject_hash(match_id, raw_participant["participantId"]),
            role=raw_participant.get("role"),
            score=parse_participant_score(raw_participant),
            final_items=parse_final_items(raw_participant),
        )
        for raw_participant in document["participants"]
    )

    frames: list[TimelineFrameDTO] = []
    events: list[TimelineEventDTO] = []
    source_index = 0
    for raw_frame in document["frames"]:
        frames.append(
            TimelineFrameDTO(
                timestamp_ms=raw_frame["timestampMs"],
                participant_frames={
                    int(participant_id): dict(participant_frame)
                    for participant_id, participant_frame in raw_frame.get(
                        "participantFrames", {}
                    ).items()
                },
            )
        )
        for raw_event in raw_frame.get("events", []):
            events.append(
                TimelineEventDTO(
                    source_index=source_index,
                    timestamp_ms=raw_event["timestampMs"],
                    type=raw_event["type"],
                    payload={
                        key: value
                        for key, value in raw_event.items()
                        if key not in {"timestampMs", "type"}
                    },
                )
            )
            source_index += 1

    return MatchSourceResult(
        match_id=match_id,
        origin=SourceOrigin.CURATED_DEMO,
        schema_version=schema_version,
        raw_hash=raw_hash,
        queue=document["queue"],
        map_id=document["mapId"],
        patch=document["patch"],
        start_at=datetime.fromisoformat(document["startAt"]),
        duration_seconds=document["durationSeconds"],
        participants=participants,
        frames=tuple(frames),
        events=tuple(events),
    )
