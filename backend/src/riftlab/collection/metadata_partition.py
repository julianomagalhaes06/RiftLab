"""Structural Match-V5 projection and deterministic CC-316 partitioning.

The complete response may arrive as opaque bytes under the approved CC-316
amendment. This scanner walks JSON syntax but only decodes allowlisted values;
unknown subtrees are skipped without constructing Python objects for them.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from riftlab.ingestion.infrastructure.riot_source import _COMPLETED_RESULT

_PATCH_PREFIX = re.compile(r"^(\d+)\.(\d+)(?:\.|$)")
_MATCH_ID = re.compile(r"^BR1_[0-9]{1,20}$")
_NUMBER = re.compile(rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


class StructuralMetadataError(ValueError):
    """Malformed or incomplete allowlisted metadata; never includes input values."""


class _Scanner:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.pos = 0
        self.length = len(body)

    def _ws(self) -> None:
        while self.pos < self.length and self.body[self.pos] in b" \t\r\n":
            self.pos += 1

    def _expect(self, token: int) -> None:
        self._ws()
        if self.pos >= self.length or self.body[self.pos] != token:
            raise StructuralMetadataError("JSON estrutural inválido")
        self.pos += 1

    def _string(self, *, decode: bool) -> str | None:
        self._ws()
        if self.pos >= self.length or self.body[self.pos] != ord('"'):
            raise StructuralMetadataError("JSON estrutural inválido")
        start = self.pos
        self.pos += 1
        escaped = False
        while self.pos < self.length:
            byte = self.body[self.pos]
            if escaped:
                if byte == ord("u"):
                    end = self.pos + 5
                    if end > self.length or any(
                        value not in b"0123456789abcdefABCDEF"
                        for value in self.body[self.pos + 1 : end]
                    ):
                        raise StructuralMetadataError("escape JSON inválido")
                    self.pos = end
                    escaped = False
                    continue
                if byte not in b'"\\/bfnrt':
                    raise StructuralMetadataError("escape JSON inválido")
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                self.pos += 1
                if not decode:
                    return None
                try:
                    import json

                    value = json.loads(self.body[start : self.pos].decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise StructuralMetadataError("string JSON inválida") from exc
                if not isinstance(value, str):
                    raise StructuralMetadataError("string JSON inválida")
                return value
            elif byte < 0x20:
                raise StructuralMetadataError("string JSON inválida")
            elif not decode and byte >= 0x80:
                self._skip_utf8_sequence()
                continue
            self.pos += 1
        raise StructuralMetadataError("string JSON incompleta")

    def _skip_utf8_sequence(self) -> None:
        """Validate UTF-8 in an opaque string without decoding/materializing it."""
        first = self.body[self.pos]
        if 0xC2 <= first <= 0xDF:
            width, second_min, second_max = 2, 0x80, 0xBF
        elif first == 0xE0:
            width, second_min, second_max = 3, 0xA0, 0xBF
        elif 0xE1 <= first <= 0xEC or 0xEE <= first <= 0xEF:
            width, second_min, second_max = 3, 0x80, 0xBF
        elif first == 0xED:
            width, second_min, second_max = 3, 0x80, 0x9F
        elif first == 0xF0:
            width, second_min, second_max = 4, 0x90, 0xBF
        elif 0xF1 <= first <= 0xF3:
            width, second_min, second_max = 4, 0x80, 0xBF
        elif first == 0xF4:
            width, second_min, second_max = 4, 0x80, 0x8F
        else:
            raise StructuralMetadataError("UTF-8 inválido em string opaca")
        end = self.pos + width
        if end > self.length:
            raise StructuralMetadataError("UTF-8 incompleto em string opaca")
        second = self.body[self.pos + 1]
        if not second_min <= second <= second_max or any(
            not 0x80 <= value <= 0xBF for value in self.body[self.pos + 2 : end]
        ):
            raise StructuralMetadataError("UTF-8 inválido em string opaca")
        self.pos = end

    def _scalar(self) -> Any:
        self._ws()
        if self.pos >= self.length:
            raise StructuralMetadataError("valor JSON ausente")
        if self.body[self.pos] == ord('"'):
            return self._string(decode=True)
        for raw, value in ((b"true", True), (b"false", False), (b"null", None)):
            if self.body.startswith(raw, self.pos):
                self.pos += len(raw)
                return value
        match = _NUMBER.match(self.body, self.pos)
        if match is None:
            raise StructuralMetadataError("valor JSON inválido")
        token = match.group()
        self.pos = match.end()
        try:
            return int(token) if b"." not in token and b"e" not in token.lower() else float(token)
        except ValueError as exc:
            raise StructuralMetadataError("número JSON inválido") from exc

    def _skip_number_or_literal(self) -> None:
        self._ws()
        if self.pos >= self.length:
            raise StructuralMetadataError("valor JSON ausente")
        for raw in (b"true", b"false", b"null"):
            if self.body.startswith(raw, self.pos):
                self.pos += len(raw)
                return
        match = _NUMBER.match(self.body, self.pos)
        if match is None:
            raise StructuralMetadataError("valor JSON inválido")
        self.pos = match.end()

    def skip_value(self, depth: int = 0) -> None:
        if depth > 128:
            raise StructuralMetadataError("JSON excede profundidade permitida")
        self._ws()
        if self.pos >= self.length:
            raise StructuralMetadataError("valor JSON ausente")
        token = self.body[self.pos]
        if token == ord('"'):
            self._string(decode=False)
        elif token == ord("{"):
            self.pos += 1
            self._ws()
            if self.pos < self.length and self.body[self.pos] == ord("}"):
                self.pos += 1
                return
            while True:
                self._string(decode=False)
                self._expect(ord(":"))
                self.skip_value(depth + 1)
                self._ws()
                if self.pos < self.length and self.body[self.pos] == ord("}"):
                    self.pos += 1
                    return
                self._expect(ord(","))
        elif token == ord("["):
            self.pos += 1
            self._ws()
            if self.pos < self.length and self.body[self.pos] == ord("]"):
                self.pos += 1
                return
            while True:
                self.skip_value(depth + 1)
                self._ws()
                if self.pos < self.length and self.body[self.pos] == ord("]"):
                    self.pos += 1
                    return
                self._expect(ord(","))
        else:
            self._skip_number_or_literal()

    def allowed_string_array(self, transform: Any) -> tuple[str, ...]:
        values: list[str] = []
        self._expect(ord("["))
        self._ws()
        if self.pos < self.length and self.body[self.pos] == ord("]"):
            self.pos += 1
            return ()
        while True:
            value = self._string(decode=True)
            assert value is not None
            values.append(transform(value))
            self._ws()
            if self.pos < self.length and self.body[self.pos] == ord("]"):
                self.pos += 1
                return tuple(values)
            self._expect(ord(","))


@dataclass(frozen=True, slots=True)
class StructuralMetadata:
    match_id: str
    patch: str
    queue_id: int
    map_id: int
    game_complete: bool
    participant_hmacs: tuple[str, ...]


def parse_structural_metadata(
    body: bytes,
    *,
    requested_match_id: str,
    subject_hash_secret: str,
    participant_callback: Callable[[str], None] | None = None,
) -> StructuralMetadata:
    """Extract only CC-316 structural fields; never return PUUIDs or raw JSON."""
    if not _MATCH_ID.fullmatch(requested_match_id):
        raise StructuralMetadataError("ID solicitado fora do escopo BR1")
    if not subject_hash_secret:
        raise StructuralMetadataError("segredo HMAC ausente")
    scanner = _Scanner(body)
    fields: dict[str, Any] = {}
    seen_sections: set[str] = set()
    pending_participants: list[str] = []

    def participant_hmac(puuid: str) -> str:
        if not puuid or len(puuid) > 256:
            raise StructuralMetadataError("identidade estrutural inválida")
        pending_participants.append(puuid)
        message = b"CC-316-participant-v1|" + puuid.encode("utf-8")
        return hmac.new(subject_hash_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    def read_object(section: str) -> None:
        scanner._expect(ord("{"))
        scanner._ws()
        if scanner.pos < scanner.length and scanner.body[scanner.pos] == ord("}"):
            scanner.pos += 1
            return
        while True:
            key = scanner._string(decode=True)
            assert key is not None
            scanner._expect(ord(":"))
            allowed: str | None = None
            if section == "metadata" and key == "matchId":
                allowed = "match_id"
            elif section == "metadata" and key == "participants":
                allowed = "participant_hmacs"
            elif section == "info" and key == "gameVersion":
                allowed = "game_version"
            elif section == "info" and key == "queueId":
                allowed = "queue_id"
            elif section == "info" and key == "mapId":
                allowed = "map_id"
            elif section == "info" and key == "endOfGameResult":
                allowed = "end_result"

            if allowed is None:
                scanner.skip_value()
            else:
                if allowed in fields:
                    raise StructuralMetadataError("campo estrutural duplicado")
                fields[allowed] = (
                    scanner.allowed_string_array(participant_hmac)
                    if allowed == "participant_hmacs"
                    else scanner._scalar()
                )
            scanner._ws()
            if scanner.pos < scanner.length and scanner.body[scanner.pos] == ord("}"):
                scanner.pos += 1
                return
            scanner._expect(ord(","))

    scanner._expect(ord("{"))
    scanner._ws()
    if scanner.pos < scanner.length and scanner.body[scanner.pos] == ord("}"):
        raise StructuralMetadataError("documento sem campos estruturais")
    while True:
        key = scanner._string(decode=True)
        assert key is not None
        scanner._expect(ord(":"))
        if key in {"metadata", "info"}:
            if key in seen_sections:
                raise StructuralMetadataError("seção estrutural duplicada")
            seen_sections.add(key)
            read_object(key)
        else:
            scanner.skip_value()
        scanner._ws()
        if scanner.pos < scanner.length and scanner.body[scanner.pos] == ord("}"):
            scanner.pos += 1
            break
        scanner._expect(ord(","))
    scanner._ws()
    if scanner.pos != scanner.length:
        raise StructuralMetadataError("conteúdo após documento JSON")

    match_id = fields.get("match_id")
    game_version = fields.get("game_version")
    queue_id = fields.get("queue_id")
    map_id = fields.get("map_id")
    result = fields.get("end_result")
    participants = fields.get("participant_hmacs")
    if match_id != requested_match_id:
        raise StructuralMetadataError("identidade da partida ausente ou divergente")
    if not isinstance(game_version, str):
        raise StructuralMetadataError("versão estrutural ausente ou inválida")
    patch_match = _PATCH_PREFIX.match(game_version)
    if patch_match is None:
        raise StructuralMetadataError("versão estrutural inválida")
    if isinstance(queue_id, bool) or not isinstance(queue_id, int):
        raise StructuralMetadataError("fila estrutural ausente ou inválida")
    if isinstance(map_id, bool) or not isinstance(map_id, int):
        raise StructuralMetadataError("mapa estrutural ausente ou inválido")
    if not isinstance(result, str):
        raise StructuralMetadataError("marcador estrutural ausente ou inválido")
    if (
        not isinstance(participants, tuple)
        or not participants
        or len(set(participants)) != len(participants)
    ):
        raise StructuralMetadataError("participantes estruturais ausentes ou duplicados")
    if participant_callback is not None:
        for puuid in pending_participants:
            participant_callback(puuid)
    return StructuralMetadata(
        match_id=match_id,
        patch=f"{patch_match.group(1)}.{patch_match.group(2)}",
        queue_id=queue_id,
        map_id=map_id,
        game_complete=result == _COMPLETED_RESULT,
        participant_hmacs=participants,
    )


@dataclass(frozen=True, slots=True)
class PartitionResult:
    exploration_ids: tuple[str, ...]
    confirmation_ids: tuple[str, ...]
    overlap_exclusions: tuple[str, ...]
    structural_exclusions: tuple[str, ...]
    status: str


def build_partition(
    records: list[StructuralMetadata],
    *,
    patch: str = "16.19",
    queue_id: int = 420,
    map_id: int = 11,
    required_exploration_matches: int = 20,
) -> PartitionResult:
    """Build deterministic CC-316 partitions; only IDs appear in the result."""
    unique: dict[str, StructuralMetadata] = {}
    structural_exclusions: list[str] = []
    for record in records:
        if record.match_id in unique:
            structural_exclusions.append(record.match_id)
            continue
        unique[record.match_id] = record

    eligible: list[StructuralMetadata] = []
    for record in unique.values():
        if (record.patch, record.queue_id, record.map_id, record.game_complete) == (
            patch,
            queue_id,
            map_id,
            True,
        ):
            eligible.append(record)
        else:
            structural_exclusions.append(record.match_id)

    eligible.sort(
        key=lambda item: (
            hashlib.sha256(f"CC-316-partition-v1|{item.match_id}".encode()).hexdigest(),
            item.match_id,
        )
    )
    exploration = eligible[:required_exploration_matches]
    potential_confirmation = eligible[required_exploration_matches:]
    if len(exploration) < required_exploration_matches:
        potential_confirmation = []
    exploration_participants = {
        participant for record in exploration for participant in record.participant_hmacs
    }
    overlaps = tuple(
        record.match_id
        for record in potential_confirmation
        if exploration_participants.intersection(record.participant_hmacs)
    )
    excluded_set = set(overlaps)
    confirmation = tuple(
        record.match_id for record in potential_confirmation if record.match_id not in excluded_set
    )
    status = "READY" if len(exploration) == required_exploration_matches else "NO_SAMPLE"
    return PartitionResult(
        exploration_ids=tuple(record.match_id for record in exploration),
        confirmation_ids=confirmation,
        overlap_exclusions=overlaps,
        structural_exclusions=tuple(structural_exclusions),
        status=status,
    )
