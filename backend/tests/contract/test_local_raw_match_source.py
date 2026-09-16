"""LocalRawMatchSource (CC-203, revisão): estruturalmente incapaz de emitir
rede — não importa httpx/RiotHttpTransport em lugar nenhum deste módulo (ver
import abaixo, nunca `import httpx`). Achado real da revisão de aa3e304:
reconstruct_match/CLI usava RiotMatchSource para --mode real, que busca da
Riot de verdade quando o cache local está ausente/incompleto, contradizendo
"reconstrução nunca coleta nova"."""

from __future__ import annotations

import json
import socket

import httpx
import pytest

from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore, RawStoreCorruptedError
from riftlab.ingestion.infrastructure.riot_source import LocalRawMatchSource, RiotMatchSource
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.shared.errors import SourceNotFoundError, SourceSchemaError

_MATCH_ID = "BR1_1000000002"
_SECRET = "segredo-local-de-teste-nao-real"  # noqa: S105
_PUUID_1 = "puuid-sintetico-1"
_PUUID_2 = "puuid-sintetico-2"


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prova comportamental (não introspecção): se LocalRawMatchSource algum
    dia passar a emitir rede de verdade, este teste falha imediatamente. O
    populamento de cache via httpx.MockTransport (RiotMatchSource) nunca
    toca socket real, então convive com este bloqueio sem problema."""

    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("chamada de rede bloqueada no teste de LocalRawMatchSource")

    monkeypatch.setattr(socket.socket, "connect", _blocked)


def _match_doc() -> dict:
    return {
        "metadata": {"matchId": _MATCH_ID, "participants": [_PUUID_1, _PUUID_2]},
        "info": {
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
        },
    }


def _timeline_doc() -> dict:
    return {
        "metadata": {"matchId": _MATCH_ID},
        "info": {
            "frameInterval": 60000,
            "participants": [
                {"participantId": 1, "puuid": _PUUID_1},
                {"participantId": 2, "puuid": _PUUID_2},
            ],
            "frames": [{"timestamp": 0, "participantFrames": {}, "events": []}],
        },
    }


async def _populate_cache_via_real_riot_pipeline(raw_dir) -> None:
    """Simula um import já concluído (CC-201): usa RiotMatchSource com
    MockTransport uma única vez só para POPULAR o cache local, exatamente
    como o worker real faria — depois disso, LocalRawMatchSource nunca mais
    precisa (nem pode) tocar rede para a mesma identidade."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/timeline"):
            return httpx.Response(200, content=json.dumps(_timeline_doc()).encode())
        return httpx.Response(200, content=json.dumps(_match_doc()).encode())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = RiotHttpTransport(
        api_key="RGAPI-fake-test-key-not-real",
        budget=RiotFetchBudget(
            max_matches=10, max_requests=20, max_attempts_per_request=1, max_total_seconds=30.0
        ),
        client=client,
    )
    store = DurableRawMatchStore(raw_dir)
    await RiotMatchSource(transport, store, _SECRET).load_match(_MATCH_ID)
    await client.aclose()


async def test_raises_not_found_when_raw_not_cached_never_touches_network(tmp_path) -> None:
    """Diretório vazio: LocalRawMatchSource não tem transporte algum para
    tentar — a ausência de cache é sempre um erro explícito, nunca um
    fallback silencioso para rede (achado da revisão)."""
    store = DurableRawMatchStore(tmp_path / "raw")
    source = LocalRawMatchSource(store, _SECRET)

    with pytest.raises(SourceNotFoundError) as excinfo:
        await source.load_match(_MATCH_ID)
    assert excinfo.value.code == "RAW_MATCH_NOT_CACHED"


async def test_loads_successfully_from_pre_populated_cache(tmp_path) -> None:
    raw_dir = tmp_path / "raw"
    await _populate_cache_via_real_riot_pipeline(raw_dir)

    store = DurableRawMatchStore(raw_dir)
    source = LocalRawMatchSource(store, _SECRET)
    result = await source.load_match(_MATCH_ID)

    assert result.match_id == _MATCH_ID
    assert {p.participant_id for p in result.participants} == {1, 2}


async def test_only_match_cached_without_timeline_still_raises_not_found(tmp_path) -> None:
    raw_dir = tmp_path / "raw"
    await _populate_cache_via_real_riot_pipeline(raw_dir)
    # Remove só o timeline para simular um raw parcialmente presente —
    # LocalRawMatchSource nunca tenta completar isso buscando da rede.
    for path in (raw_dir).rglob("*timeline*"):
        path.unlink()

    store = DurableRawMatchStore(raw_dir)
    source = LocalRawMatchSource(store, _SECRET)
    with pytest.raises(SourceNotFoundError) as excinfo:
        await source.load_match(_MATCH_ID)
    assert excinfo.value.code == "RAW_MATCH_NOT_CACHED"


async def test_corrupted_local_raw_raises_schema_error_not_silent_refetch(tmp_path) -> None:
    raw_dir = tmp_path / "raw"
    await _populate_cache_via_real_riot_pipeline(raw_dir)
    # Corrompe um blob referenciado: hash local diverge do manifesto.
    corrupted = False
    for path in raw_dir.rglob("*.json"):
        if "blobs" in path.parts:
            path.write_bytes(b'{"corrupted": true}')
            corrupted = True
            break
    assert corrupted, "fixture do teste não encontrou um blob para corromper"

    store = DurableRawMatchStore(raw_dir)
    source = LocalRawMatchSource(store, _SECRET)
    with pytest.raises((SourceSchemaError, RawStoreCorruptedError)):
        await source.load_match(_MATCH_ID)
