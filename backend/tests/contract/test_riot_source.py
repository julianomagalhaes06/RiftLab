"""RiotMatchSource (CC-201): Match-V5 + Timeline-V5 reais via httpx.MockTransport
(nenhuma chamada de rede real, nenhuma credencial real) — mesma metodologia de
test_riot_transport.py (CC-105). max_attempts_per_request=1: erro transitório
surge imediato para o worker decidir, sem retry/sleep em memória aqui."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore, RawStoreCorruptedError
from riftlab.ingestion.infrastructure.riot_source import RiotMatchSource
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.ingestion.infrastructure.subject_hash import SubjectHashSecretMissingError
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import (
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
    SourceSchemaError,
)

_MATCH_ID = "BR1_1000000001"
_SECRET = "segredo-local-de-teste-nao-real"  # noqa: S105
_PUUID_1 = "puuid-sintetico-1"
_PUUID_2 = "puuid-sintetico-2"


def _match_doc(**overrides: object) -> dict:
    info = {
        "queueId": 420,
        "mapId": 11,
        "gameVersion": "16.17.123.4567",
        "gameStartTimestamp": 1_700_000_000_000,
        "gameDuration": 1800,
        "endOfGameResult": "GameComplete",
        "participants": [
            {
                "participantId": 1,
                "puuid": _PUUID_1,
                "championId": 89,
                "teamId": 100,
                "teamPosition": "UTILITY",
            },
            {
                "participantId": 2,
                "puuid": _PUUID_2,
                "championId": 51,
                "teamId": 200,
                "teamPosition": "BOTTOM",
            },
        ],
    }
    info.update(overrides)
    return {"metadata": {"matchId": _MATCH_ID, "participants": [_PUUID_1, _PUUID_2]}, "info": info}


def _timeline_doc(**overrides: object) -> dict:
    info = {
        "frameInterval": 60000,
        "participants": [
            {"participantId": 1, "puuid": _PUUID_1},
            {"participantId": 2, "puuid": _PUUID_2},
        ],
        "frames": [
            {
                "timestamp": 0,
                "participantFrames": {"1": {"currentGold": 500}, "2": {"currentGold": 500}},
                "events": [],
            },
            {
                "timestamp": 60000,
                "participantFrames": {"1": {"currentGold": 800}, "2": {"currentGold": 600}},
                "events": [
                    {
                        "timestamp": 65000,
                        "type": "ITEM_PURCHASED",
                        "participantId": 1,
                        "itemId": 1001,
                    },
                    {
                        "timestamp": 66000,
                        "type": "ITEM_PURCHASED",
                        "participantId": 2,
                        "itemId": 1002,
                    },
                ],
            },
        ],
    }
    info.update(overrides)
    return {"metadata": {"matchId": _MATCH_ID}, "info": info}


class _RoutedHandler:
    """Roteia por path (match vs timeline) e conta chamadas por kind — usado
    para provar que o armazenamento durável evita nova rede num segundo
    load_match do mesmo match_id."""

    def __init__(self, match_doc: dict | None, timeline_doc: dict | None) -> None:
        self.match_doc = match_doc
        self.timeline_doc = timeline_doc
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/timeline"):
            self.calls.append("timeline")
            if self.timeline_doc is None:
                return httpx.Response(404)
            return httpx.Response(200, content=json.dumps(self.timeline_doc).encode())
        self.calls.append("match")
        if self.match_doc is None:
            return httpx.Response(404)
        return httpx.Response(200, content=json.dumps(self.match_doc).encode())


def _source(handler: _RoutedHandler, raw_dir, secret: str | None = _SECRET) -> RiotMatchSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = RiotHttpTransport(
        api_key="RGAPI-fake-test-key-not-real",
        budget=RiotFetchBudget(
            max_matches=10, max_requests=20, max_attempts_per_request=1, max_total_seconds=30.0
        ),
        client=client,
    )
    store = DurableRawMatchStore(raw_dir)
    return RiotMatchSource(transport, store, secret)


async def test_load_match_success_maps_participants_frames_events(tmp_path) -> None:
    handler = _RoutedHandler(_match_doc(), _timeline_doc())
    source = _source(handler, tmp_path / "raw")

    result = await source.load_match(_MATCH_ID)

    assert result.match_id == _MATCH_ID
    assert result.origin == SourceOrigin.COMPLETED_MATCH
    assert result.queue == 420
    assert result.map_id == 11
    assert result.patch == "16.17"
    assert result.duration_seconds == 1800
    assert len(result.participants) == 2
    assert len(result.frames) == 2
    assert len(result.events) == 2
    assert [e.source_index for e in result.events] == [0, 1]

    p1 = next(p for p in result.participants if p.participant_id == 1)
    assert p1.champion_id == 89
    assert p1.team == 100
    assert p1.role == "UTILITY"
    expected_hash = hmac.new(_SECRET.encode(), _PUUID_1.encode(), hashlib.sha256).hexdigest()
    assert p1.subject_hash == expected_hash
    assert _PUUID_1 not in repr(p1)


async def test_load_match_rejects_incomplete_match(tmp_path) -> None:
    handler = _RoutedHandler(_match_doc(endOfGameResult="Abort_AntiCheatExit"), _timeline_doc())
    source = _source(handler, tmp_path / "raw")

    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "MATCH_NOT_COMPLETED"


async def test_load_match_rejects_identity_mismatch_between_documents(tmp_path) -> None:
    other_match_doc = _match_doc()
    other_match_doc["metadata"]["matchId"] = "BR1_9999999999"
    handler = _RoutedHandler(other_match_doc, _timeline_doc())
    source = _source(handler, tmp_path / "raw")

    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "MATCH_SOURCE_IDENTITY_MISMATCH"


async def test_load_match_rejects_participant_set_mismatch(tmp_path) -> None:
    mismatched_timeline = _timeline_doc()
    mismatched_timeline["info"]["participants"] = [{"participantId": 1, "puuid": _PUUID_1}]
    handler = _RoutedHandler(_match_doc(), mismatched_timeline)
    source = _source(handler, tmp_path / "raw")

    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "MATCH_TIMELINE_IDENTITY_MISMATCH"


async def test_load_match_rejects_timeline_with_empty_info(tmp_path) -> None:
    """Achado real da revisão CC-201 (missing_timeline_structure_accepted=True
    no probe): timeline {metadata:{...}, info:{}} passava como sucesso com
    zero frames/eventos — frames/participants deixaram de ter default []."""
    empty_timeline = {"metadata": _timeline_doc()["metadata"], "info": {}}
    handler = _RoutedHandler(_match_doc(), empty_timeline)
    source = _source(handler, tmp_path / "raw")

    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "MATCH_SOURCE_MISSING_FIELD"


async def test_load_match_rejects_puuid_mismatch_for_same_participant_id(tmp_path) -> None:
    """Achado real da revisão CC-201 (mismatched_timeline_puuid_accepted=True
    no probe): o mesmo participantId com PUUID diferente entre match e
    timeline passava — só o conjunto de participantId era comparado antes."""
    mismatched_timeline = _timeline_doc()
    mismatched_timeline["info"]["participants"][0]["puuid"] = "DIFFERENT_SYNTHETIC_SUBJECT"
    handler = _RoutedHandler(_match_doc(), mismatched_timeline)
    source = _source(handler, tmp_path / "raw")

    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "MATCH_TIMELINE_IDENTITY_MISMATCH"


async def test_load_match_rejects_missing_champion_id_without_raw_exception(tmp_path) -> None:
    """Achado real da revisão CC-201 (missing_champion_exception=KeyError no
    probe): campo obrigatório por participante ausente lançava KeyError cru
    em vez de SourceSchemaError — job ficaria preso em RUNNING até lease/
    tentativas se isto escapasse do worker sem classificação."""
    broken = _match_doc()
    del broken["info"]["participants"][0]["championId"]
    handler = _RoutedHandler(broken, _timeline_doc())
    source = _source(handler, tmp_path / "raw")

    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "MATCH_SOURCE_MISSING_FIELD"


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        (
            "negative_duration",
            lambda m, t: m["info"].__setitem__("gameDuration", -1),
        ),
        (
            "champion_wrong_type",
            lambda m, t: m["info"]["participants"][0].__setitem__("championId", "invalid"),
        ),
        (
            "champion_bool_disguised_as_int",
            lambda m, t: m["info"]["participants"][0].__setitem__("championId", True),
        ),
        (
            "frame_invalid_key",
            lambda m, t: t["info"]["frames"][0].__setitem__("participantFrames", {"bad-key": {}}),
        ),
        (
            "frame_key_superscript_digit",  # str.isdigit() aceita, int() levanta ValueError cru
            lambda m, t: t["info"]["frames"][0].__setitem__("participantFrames", {"²": {}}),
        ),
        (
            "frame_key_non_ascii_digit",  # int("٣") == 3: virava participante 3 em silêncio
            lambda m, t: t["info"]["frames"][0].__setitem__("participantFrames", {"٣": {}}),
        ),
        (
            "timestamp_overflow",
            lambda m, t: m["info"].__setitem__("gameStartTimestamp", 10**100),
        ),
        (
            "queue_wrong_type",
            lambda m, t: m["info"].__setitem__("queueId", "420"),
        ),
        (
            "game_version_wrong_type",
            lambda m, t: m["info"].__setitem__("gameVersion", 16.17),
        ),
        (
            "event_timestamp_negative",
            lambda m, t: t["info"]["frames"][1]["events"][0].__setitem__("timestamp", -1),
        ),
        (
            "event_type_wrong_type",
            lambda m, t: t["info"]["frames"][1]["events"][0].__setitem__("type", 123),
        ),
        (
            "participant_frames_not_a_dict",
            lambda m, t: t["info"]["frames"][0].__setitem__("participantFrames", ["not-a-dict"]),
        ),
    ],
)
async def test_load_match_rejects_invalid_field_types_and_limits_without_raw_exception(
    tmp_path, name: str, mutate
) -> None:
    """Achado real da revisão complementar CC-201 (review_cc201_correction_probe.py):
    duração negativa e championId de tipo errado eram aceitos sem checagem
    (só quebrariam tarde, numa constraint do banco, com o job preso em
    RUNNING até expirar/repetir); chave de participantFrames não numérica
    vazava ValueError cru de int(pid); timestamp de início absurdo vazava
    OverflowError cru de datetime.fromtimestamp — nenhum dos dois coberto
    por _STRUCTURAL_ACCESS_ERRORS. Todos agora viram SourceSchemaError
    permanente e classificado, nunca uma exceção crua nem um valor
    silenciosamente aceito."""
    match_doc = _match_doc()
    timeline_doc = _timeline_doc()
    mutate(match_doc, timeline_doc)
    handler = _RoutedHandler(match_doc, timeline_doc)
    source = _source(handler, tmp_path / "raw" / name)

    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "MATCH_SOURCE_MISSING_FIELD"


async def test_load_match_still_accepts_valid_synthetic_payload_after_validation_hardening(
    tmp_path,
) -> None:
    """Prova que a validação explícita adicionada acima não passou a rejeitar
    o payload sintético válido de baseline (mesmo formato usado por todos os
    outros testes deste arquivo)."""
    handler = _RoutedHandler(_match_doc(), _timeline_doc())
    source = _source(handler, tmp_path / "raw")

    result = await source.load_match(_MATCH_ID)
    assert result.duration_seconds == 1800
    assert result.participants[0].champion_id == 89


async def test_load_match_rejects_duplicate_participant_id_in_match(tmp_path) -> None:
    duplicated = _match_doc()
    duplicated["info"]["participants"][1]["participantId"] = 1
    handler = _RoutedHandler(duplicated, _timeline_doc())
    source = _source(handler, tmp_path / "raw")

    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "MATCH_SOURCE_MISSING_FIELD"


async def test_load_match_rejects_empty_frames_list(tmp_path) -> None:
    truncated_timeline = _timeline_doc()
    truncated_timeline["info"]["frames"] = []
    handler = _RoutedHandler(_match_doc(), truncated_timeline)
    source = _source(handler, tmp_path / "raw")

    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "MATCH_SOURCE_MISSING_FIELD"


async def test_load_match_rejects_frame_with_missing_timestamp_without_raw_exception(
    tmp_path,
) -> None:
    broken_timeline = _timeline_doc()
    del broken_timeline["info"]["frames"][0]["timestamp"]
    handler = _RoutedHandler(_match_doc(), broken_timeline)
    source = _source(handler, tmp_path / "raw")

    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "MATCH_SOURCE_MISSING_FIELD"


async def test_load_match_rejects_metadata_of_wrong_type_without_raw_exception(tmp_path) -> None:
    """TypeError/AttributeError de acesso em profundidade (ex.: metadata que
    não é objeto) também nunca escapa cru — mesma classificação."""
    broken = _match_doc()
    broken["metadata"] = "nao-e-um-objeto"
    handler = _RoutedHandler(broken, _timeline_doc())
    source = _source(handler, tmp_path / "raw")

    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "MATCH_SOURCE_MISSING_FIELD"


async def test_load_match_rejects_missing_required_field(tmp_path) -> None:
    broken = _match_doc()
    del broken["info"]["gameDuration"]
    handler = _RoutedHandler(broken, _timeline_doc())
    source = _source(handler, tmp_path / "raw")

    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "MATCH_SOURCE_MISSING_FIELD"


async def test_load_match_requires_subject_hash_secret_for_real_mode(tmp_path) -> None:
    handler = _RoutedHandler(_match_doc(), _timeline_doc())
    source = _source(handler, tmp_path / "raw", secret=None)

    with pytest.raises(SubjectHashSecretMissingError):
        await source.load_match(_MATCH_ID)


@pytest.mark.parametrize("status", [401, 403])
async def test_load_match_propagates_permanent_auth_error(tmp_path, status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    source = _source(handler, tmp_path / "raw")
    with pytest.raises(SourceAuthError):
        await source.load_match(_MATCH_ID)


async def test_load_match_propagates_permanent_not_found(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    source = _source(handler, tmp_path / "raw")
    with pytest.raises(SourceNotFoundError):
        await source.load_match(_MATCH_ID)


async def test_load_match_propagates_transient_rate_limit_without_internal_retry(tmp_path) -> None:
    """max_attempts_per_request=1: nenhuma espera em memória aqui — o erro
    transitório surge imediato, com retry_after_seconds preservado para o
    worker agendar retry_at durável."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "5"})

    source = _source(handler, tmp_path / "raw")
    with pytest.raises(SourceRateLimitedError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.retry_after_seconds == 5.0
    assert calls == 1


async def test_load_match_propagates_transient_5xx_without_internal_retry(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    source = _source(handler, tmp_path / "raw")
    with pytest.raises(SourceError):
        await source.load_match(_MATCH_ID)
    assert calls == 1


async def test_second_load_match_reuses_durable_raw_store_without_network(tmp_path) -> None:
    handler = _RoutedHandler(_match_doc(), _timeline_doc())
    raw_dir = tmp_path / "raw"
    source_a = _source(handler, raw_dir)
    await source_a.load_match(_MATCH_ID)
    assert handler.calls == ["match", "timeline"]

    # Novo transporte/adapter (simula worker retomado após crash) sobre o
    # MESMO diretório durável: reaproveita os arquivos, nenhuma nova chamada.
    handler.match_doc = None  # se tentasse rede de novo, receberia 404 e falharia
    handler.timeline_doc = None
    source_b = _source(handler, raw_dir)
    result = await source_b.load_match(_MATCH_ID)

    assert handler.calls == ["match", "timeline"]  # sem novas chamadas
    assert result.match_id == _MATCH_ID


def _corrupt_stored_blob(raw_dir, match_id: str, kind: str) -> None:
    """Corrompe o blob endereçado por conteúdo referenciado pelo ponteiro
    atual — layout novo da revisão CC-201 (blobs/<sha256>.json + <id>.<kind>.ref),
    substitui o antigo "<id>.<kind>.json" direto."""
    referenced_hash = (raw_dir / f"{match_id}.{kind}.ref").read_text().strip()
    (raw_dir / "blobs" / f"{referenced_hash}.json").write_bytes(b'{"tampered": true}')


async def test_corrupted_raw_store_is_reported_not_silently_refetched(tmp_path) -> None:
    handler = _RoutedHandler(_match_doc(), _timeline_doc())
    raw_dir = tmp_path / "raw"
    source = _source(handler, raw_dir)
    await source.load_match(_MATCH_ID)

    _corrupt_stored_blob(raw_dir, _MATCH_ID, "match")

    handler.calls.clear()
    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "RAW_STORE_CORRUPTED"
    assert handler.calls == []  # nunca tenta rede por cima de dado corrompido


async def test_corrupted_raw_store_raises_typed_error_not_generic_exception(tmp_path) -> None:
    raw_dir = tmp_path / "raw"
    store = DurableRawMatchStore(raw_dir)
    body = b'{"a": 1}'
    store.store(_MATCH_ID, "match", body, hashlib.sha256(body).hexdigest())
    _corrupt_stored_blob(raw_dir, _MATCH_ID, "match")

    with pytest.raises(RawStoreCorruptedError):
        store.read_if_valid(_MATCH_ID, "match")


async def test_legacy_partial_raw_pair_never_triggers_silent_refetch(tmp_path) -> None:
    """Achado real da revisão complementar (CC-201-REVIEW-3): um par legado
    incompleto (só corpo ou só sidecar .sha256, formato anterior à correção
    de 74224ab) era confundido com "nunca coletado" pelo raw_store e o
    adapter refazia rede por cima. Agora vira RAW_STORE_CORRUPTED imediato,
    o adapter nunca chega a chamar o transporte."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / f"{_MATCH_ID}.match.json").write_bytes(b'{"legacy": true}')  # sem o .sha256

    handler = _RoutedHandler(_match_doc(), _timeline_doc())
    source = _source(handler, raw_dir)

    with pytest.raises(SourceSchemaError) as exc_info:
        await source.load_match(_MATCH_ID)
    assert exc_info.value.code == "RAW_STORE_CORRUPTED"
    assert handler.calls == []  # nunca tenta rede por cima de dado legado incompleto
