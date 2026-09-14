"""Contrato do Protocol MatchSource (CC-104, docs/domain-model.md). Exercita o
consumidor através da interface com duas implementações locais (DemoMatchSource e
um fake mínimo), carga repetida, cenários válidos e todos os caminhos de erro do
card: fixture inválida, ausência esperada de dado, identificador fora do escopo."""

from __future__ import annotations

import hashlib
import socket
from importlib import resources

import pytest

from riftlab.ingestion.domain import MatchSource, MatchSourceResult
from riftlab.ingestion.infrastructure.demo_source import (
    DemoMatchSource,
    parse_match_source_document,
)
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import SourceNotFoundError, SourceSchemaError


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """DemoMatchSource nunca deve abrir socket algum; uma tentativa de rede aqui é
    um bug real, não motivo para mais um mock."""

    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("chamada de rede bloqueada no teste offline de MatchSource")

    monkeypatch.setattr(socket.socket, "connect", _blocked)


class FakeMatchSource:
    """Implementação mínima do Protocol, sem I/O algum — prova que o consumidor
    depende só da interface, não da fonte demo concreta."""

    def __init__(self, results: dict[str, MatchSourceResult]) -> None:
        self._results = results

    async def load_match(self, match_id: str) -> MatchSourceResult:
        try:
            return self._results[match_id]
        except KeyError as exc:
            raise SourceNotFoundError(code="MATCH_SOURCE_NOT_FOUND", detail=match_id) from exc


async def _consume_match_source(source: MatchSource, match_id: str) -> MatchSourceResult:
    """Consumidor tipado só no Protocol (verificado por `uv run mypy src`); não
    sabe se recebeu a fonte demo ou o fake mínimo — é isso que a troca de fonte
    sem mudança de domínio (critério de aceite do card) exige na prática."""
    return await source.load_match(match_id)


async def test_demo_and_fake_implementations_are_interchangeable_via_the_protocol() -> None:
    demo_result = await _consume_match_source(DemoMatchSource(), "DEMO-0001")

    fake_result = await _consume_match_source(
        FakeMatchSource({"FAKE-0001": demo_result}), "FAKE-0001"
    )

    assert fake_result == demo_result


async def test_demo_match_source_loads_simple_purchase_scenario() -> None:
    result = await DemoMatchSource().load_match("DEMO-0001")

    assert result.match_id == "DEMO-0001"
    assert result.origin is SourceOrigin.CURATED_DEMO
    assert result.schema_version == "v1"
    assert result.patch == "DEMO-1"
    assert len(result.participants) == 2
    assert [event.type for event in result.events] == ["ITEM_PURCHASED"]
    assert result.events[0].payload["itemId"] == 100001


async def test_demo_match_source_preserves_component_multiplicity_and_same_timestamp_order() -> (
    None
):
    result = await DemoMatchSource().load_match("DEMO-0002")

    ordered = [(e.source_index, e.timestamp_ms, e.payload["itemId"]) for e in result.events]
    assert ordered == [
        (0, 470000, 100001),
        (1, 470000, 100001),
        (2, 471000, 100002),
        (3, 472000, 100010),
    ]


async def test_demo_match_source_preserves_sale_event() -> None:
    result = await DemoMatchSource().load_match("DEMO-0003")

    sales = [event for event in result.events if event.type == "ITEM_SOLD"]
    assert len(sales) == 1
    assert sales[0].payload["goldGain"] == 280


async def test_demo_match_source_preserves_undo_correlation_without_collapsing_events() -> None:
    result = await DemoMatchSource().load_match("DEMO-0004")

    assert [event.type for event in result.events] == ["ITEM_PURCHASED", "ITEM_UNDO"]
    assert result.events[1].payload["beforeId"] == 100002


async def test_demo_match_source_preserves_missing_frame_as_absence_not_fabrication() -> None:
    result = await DemoMatchSource().load_match("DEMO-0005")

    frame_with_event = result.frames[1]
    assert frame_with_event.participant_frames == {}
    assert len(result.events) == 1


async def test_demo_match_source_missing_required_patch_fails_identifiably() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        await DemoMatchSource().load_match("DEMO-0006")

    assert excinfo.value.code == "MATCH_SOURCE_MISSING_FIELD"
    assert "patch" in str(excinfo.value)


async def test_demo_match_source_preserves_repeated_event_without_deduplicating() -> None:
    result = await DemoMatchSource().load_match("DEMO-0007")

    assert len(result.events) == 2
    assert result.events[0].source_index != result.events[1].source_index
    assert result.events[0].timestamp_ms == result.events[1].timestamp_ms == 54000
    assert result.events[0].payload == result.events[1].payload


async def test_demo_match_source_rejects_unknown_identifier_never_falls_back() -> None:
    with pytest.raises(SourceNotFoundError):
        # Formato parecido com um match ID real (docs/contracts.md) não convence a
        # fonte demo a inventar uma resposta: só o manifesto fechado é servido.
        await DemoMatchSource().load_match("BR1_9999999999")


async def test_demo_match_source_repeated_calls_are_independent_and_deterministic() -> None:
    source = DemoMatchSource()

    first = await source.load_match("DEMO-0001")
    second = await source.load_match("DEMO-0001")

    assert first == second
    assert first.events[0].payload is not second.events[0].payload


async def test_demo_match_source_result_is_immutable() -> None:
    result = await DemoMatchSource().load_match("DEMO-0001")

    with pytest.raises(AttributeError):
        result.match_id = "DEMO-CHANGED"  # type: ignore[misc]


async def test_demo_match_source_hash_reflects_raw_bytes_read() -> None:
    result = await DemoMatchSource().load_match("DEMO-0001")

    raw = (
        resources.files("riftlab.ingestion.infrastructure.fixtures")
        .joinpath("matches", "demo_0001_compra_simples.json")
        .read_bytes()
    )
    assert result.raw_hash == hashlib.sha256(raw).hexdigest()


def test_parse_match_source_document_rejects_invalid_json() -> None:
    with pytest.raises(SourceSchemaError) as excinfo:
        parse_match_source_document("DEMO-X", b"{not valid json")

    assert excinfo.value.code == "MATCH_SOURCE_INVALID_JSON"


def test_parse_match_source_document_rejects_unknown_schema_version() -> None:
    body = b'{"schemaVersion": "v99"}'

    with pytest.raises(SourceSchemaError) as excinfo:
        parse_match_source_document("DEMO-X", body)

    assert excinfo.value.code == "SCHEMA_UNSUPPORTED"


def test_parse_match_source_document_rejects_identity_mismatch() -> None:
    body = (
        b'{"schemaVersion":"v1","matchId":"OTHER","queue":420,"mapId":11,'
        b'"patch":"DEMO-1","startAt":"2026-01-01T00:00:00+00:00",'
        b'"durationSeconds":1,"participants":[],"frames":[]}'
    )

    with pytest.raises(SourceSchemaError) as excinfo:
        parse_match_source_document("DEMO-0001", body)

    assert excinfo.value.code == "MATCH_SOURCE_IDENTITY_MISMATCH"


def test_source_origin_never_admits_a_live_value() -> None:
    # RF17/docs/architecture.md: só completed_match ou curated_demo são origens
    # válidas para criar uma decisão; uma partida "ao vivo" nunca é um valor do tipo.
    assert {member.value for member in SourceOrigin} == {"curated_demo", "completed_match"}


def test_parse_match_source_document_never_echoes_raw_body_in_error() -> None:
    secret_looking_body = b'{"schemaVersion": "v1", "apiKey": "sk-should-never-leak"}'

    with pytest.raises(SourceSchemaError) as excinfo:
        parse_match_source_document("DEMO-X", secret_looking_body)

    assert "sk-should-never-leak" not in str(excinfo.value)
