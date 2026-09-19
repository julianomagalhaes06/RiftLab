"""GET /api/v1/matches/{matchId}/decisions e GET /api/v1/decisions/{decisionId}
(CC-204) contra PostgreSQL real e a app real: pipeline completo (sync
demo, import demo, reconstruct v6, extract) e leitura pela API. As rotas só
leem: nenhuma reconstrução, extração, fonte, rede nem escrita. Modo Estudo: o
corpo nunca revela o item comprado."""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from pipeline_helpers import DEMO_1_MAPPING, import_demo_match, sync_demo_catalog
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from riftlab.decisions import application as decisions_application
from riftlab.decisions import queries as decisions_queries
from riftlab.decisions import selection as decisions_selection
from riftlab.decisions.application import extract_decisions
from riftlab.ingestion.infrastructure.demo_source import DemoMatchSource
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import unit_of_work
from riftlab.timeline import application as timeline_application
from riftlab.timeline.application import reconstruct_match
from riftlab.timeline.domain import TimelineEvent
from riftlab.timeline.repository import SqlAlchemyTimelineRepository

_NO_SCOPE_FILE = Path("/nonexistent/riftlab-scope.yaml")  # DEMO nunca lê este arquivo
_FORBIDDEN_KEYS = {
    "targetItemId",
    "targetClass",
    "retrospectiveTarget",
    "purchasedItemId",
    "features",
    "featureVector",
    "quality",
    "uncertain",
    "subjectHash",
    "subject_hash",
}
_PROBLEM_KEYS = {"type", "title", "status", "code", "detail", "requestId"}


def _listing(match_id: str) -> str:
    return f"/api/v1/matches/{match_id}/decisions"


async def _pipeline(
    session_factory, tmp_path, match_id: str, extra_events: list[TimelineEvent] | None = None
) -> None:
    """sync demo -> import demo -> (eventos extras opcionais) -> reconstruct v6 -> extract."""
    await sync_demo_catalog(session_factory)
    await import_demo_match(session_factory, tmp_path, match_id)
    if extra_events:
        async with unit_of_work(session_factory) as session:
            timeline = SqlAlchemyTimelineRepository(session)
            for event in extra_events:
                await timeline.add_event(event)
    await reconstruct_match(session_factory, DEMO_1_MAPPING, DemoMatchSource(), match_id)
    await extract_decisions(session_factory, match_id, scope_path=_NO_SCOPE_FILE)


def _purchase(match_id: str, source_index: int, timestamp_ms: int, item_id: int) -> TimelineEvent:
    payload = {"participantId": 1, "itemId": item_id}
    return TimelineEvent(match_id, source_index, timestamp_ms, "ITEM_PURCHASED", payload)


def _walk(value: Any):
    """Todas as chaves e todos os valores textuais de um JSON."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _assert_pre_decision_only(body: Any) -> None:
    keys = {key for key, _ in _walk(body)}
    assert not keys & _FORBIDDEN_KEYS, keys & _FORBIDDEN_KEYS


def _assert_problem(response, status: int, code: str, echoed: str | None = None) -> None:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert set(body) == _PROBLEM_KEYS
    assert body["code"] == code and body["status"] == status
    if echoed:  # nunca ecoa o que o cliente enviou
        assert echoed not in response.text


async def _all_pages(client, match_id: str, participant: int, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(100):  # teto de segurança
        params: dict[str, Any] = {"participantId": participant, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = await client.get(_listing(match_id), params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["items"]) <= limit
        if page["nextCursor"] is not None:
            assert page["items"], "página vazia com nextCursor"
            assert page["nextCursor"] != ""
        items.extend(page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            return items
    raise AssertionError("paginação não terminou")


# ---------------------------------------------------------------------------
# DEMO-0002 e DEMO-0008 pela API
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_demo_0002_list_and_detail_through_the_api(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await _pipeline(session_factory, tmp_path, "DEMO-0002")
    listing = await app_client.get(_listing("DEMO-0002"), params={"participantId": 1})
    assert listing.status_code == 200
    page = listing.json()
    assert page["nextCursor"] is None and len(page["items"]) == 1
    [item] = page["items"]
    assert (item["participantId"], item["eventSourceIndex"], item["timestampMs"]) == (1, 3, 472_000)
    assert item["eligible"] is True and item["reasonCodes"] == [] and item["mainItemOrdinal"] == 1
    assert item["budgetSource"] == "FRAME_ESTIMATE" and item["frameAgeMs"] == 472_000
    assert item["staleFrame"] is True
    _assert_pre_decision_only(page)

    detail = await app_client.get(f"/api/v1/decisions/{item['decisionId']}")
    assert detail.status_code == 200
    body = detail.json()
    _assert_pre_decision_only(body)
    assert body["decisionId"] == item["decisionId"] and body["matchId"] == "DEMO-0002"
    assert body["eligible"] is True and body["reasons"] == []
    assert body["inventory"] == {  # multiconjunto: dois itens iguais = quantity 2
        "items": [  # nome do catálogo fixado pelo ponto (CC-205, E3)
            {"itemId": 100001, "quantity": 2, "name": "Componente Sintético A"},
            {"itemId": 100002, "quantity": 1, "name": "Componente Sintético B"},
        ],
        "trinketItemId": None,
    }
    assert body["budget"] == {
        "value": 500,
        "source": "FRAME_ESTIMATE",
        "frameAgeMs": 472_000,
        "staleFrame": True,
        "codes": ["STALE_FRAME"],
        "immediatePurchaseAssertable": False,
    }
    assert body["level"] == {"value": 1, "frameAgeMs": 472_000, "absentReason": None}
    assert body["versions"]["selectionVersion"] == "decision_selection_v1"
    assert body["versions"]["reconstructionVersion"] == 6
    assert body["versions"]["scopeVersion"] == "demo_scope_v1"
    assert body["versions"]["curationRulesVersion"] == "curated_demo_v1"
    assert "100010" not in detail.text and "100010" not in listing.text  # o alvo nunca aparece


@pytest.mark.parametrize("_demo_match", ["DEMO-0008"], indirect=True)
async def test_demo_0008_real_form_through_the_api(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await _pipeline(session_factory, tmp_path, "DEMO-0008")
    listing = await app_client.get(_listing("DEMO-0008"), params={"participantId": 1})
    [item] = listing.json()["items"]
    assert item["eligible"] is True and item["staleFrame"] is False and item["frameAgeMs"] == 20_000
    body = (await app_client.get(f"/api/v1/decisions/{item['decisionId']}")).json()
    assert body["inventory"]["items"] == [
        {"itemId": 100001, "quantity": 2, "name": "Componente Sintético A"},
        {"itemId": 100002, "quantity": 1, "name": "Componente Sintético B"},
    ]
    assert body["budget"]["value"] == 640 and body["budget"]["codes"] == []
    assert body["budget"]["immediatePurchaseAssertable"] is True
    assert body["level"]["value"] == 7
    _assert_pre_decision_only(body)


# ---------------------------------------------------------------------------
# Paginação estável
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_pagination_over_tied_timestamps_never_repeats_or_loses(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    match_id = "DEMO-0002"
    # 12 compras do item principal no MESMO timestamp (source_index diferentes)
    extra = [_purchase(match_id, 4 + n, 480_000, 100010) for n in range(12)]
    await _pipeline(session_factory, tmp_path, match_id, extra)

    everything = await _all_pages(app_client, match_id, 1, 100)
    ids = [item["decisionId"] for item in everything]
    assert len(ids) == 13 and len(set(ids)) == 13
    order = [(i["timestampMs"], i["eventSourceIndex"]) for i in everything]
    assert order == sorted(order)  # ordem estável (timestamp, source_index)
    assert sum(1 for i in everything if i["timestampMs"] == 480_000) == 12  # empate real
    for limit in (1, 2, 3, 5, 7, 12, 13, 14, 100):
        paged = await _all_pages(app_client, match_id, 1, limit)
        assert [i["decisionId"] for i in paged] == ids, f"limit={limit}"
    single = await app_client.get(_listing(match_id), params={"participantId": 1, "limit": 13})
    assert single.json()["nextCursor"] is None  # limite exato: nenhuma página vazia extra
    assert everything[-1]["mainItemOrdinal"] >= 3  # inelegíveis também são listados
    ineligible = [i for i in everything if not i["eligible"]]
    assert ineligible and all(
        "OUT_OF_SCOPE" in i["reasonCodes"] for i in ineligible
    )  # ordinal >= 3


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_new_extraction_between_pages_does_not_change_the_pinned_extraction(
    app_client, session_factory, tmp_path, _demo_match, monkeypatch
) -> None:
    match_id = "DEMO-0002"
    await _pipeline(
        session_factory,
        tmp_path,
        match_id,
        [_purchase(match_id, 4 + n, 480_000, 100010) for n in range(5)],
    )
    original = [i["decisionId"] for i in await _all_pages(app_client, match_id, 1, 100)]
    first = (
        await app_client.get(_listing(match_id), params={"participantId": 1, "limit": 2})
    ).json()
    assert first["nextCursor"] is not None

    # outra tupla de versões vira a extração CORRENTE entre as páginas
    monkeypatch.setattr(decisions_selection, "DECISION_SELECTION_VERSION", "decision_selection_v2")
    await extract_decisions(session_factory, match_id, scope_path=_NO_SCOPE_FILE)
    fresh = [i["decisionId"] for i in await _all_pages(app_client, match_id, 1, 100)]
    assert set(fresh).isdisjoint(original)  # sem cursor: a extração nova

    rest: list[str] = [i["decisionId"] for i in first["items"]]
    cursor = first["nextCursor"]
    while cursor:
        page = (
            await app_client.get(
                _listing(match_id), params={"participantId": 1, "limit": 2, "cursor": cursor}
            )
        ).json()
        rest.extend(i["decisionId"] for i in page["items"])
        cursor = page["nextCursor"]
    assert rest == original  # com cursor: a extração fixada nele, sem misturar

    # o detalhe de um ponto antigo segue disponível, com as versões dele
    old_detail = (await app_client.get(f"/api/v1/decisions/{original[0]}")).json()
    assert old_detail["versions"]["selectionVersion"] == "decision_selection_v1"
    new_detail = (await app_client.get(f"/api/v1/decisions/{fresh[0]}")).json()
    assert new_detail["versions"]["selectionVersion"] == "decision_selection_v2"


# ---------------------------------------------------------------------------
# Erros: 404 / 409 / 422 em problem+json
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_not_prepared_is_409_and_zero_points_is_200_with_empty_items(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    match_id = "DEMO-0002"
    await sync_demo_catalog(session_factory)
    await import_demo_match(session_factory, tmp_path, match_id)
    # importada, NÃO reconstruída
    _assert_problem(
        await app_client.get(_listing(match_id), params={"participantId": 1}),
        409,
        "DECISIONS_NOT_PREPARED",
    )
    # reconstruída, NÃO extraída
    await reconstruct_match(session_factory, DEMO_1_MAPPING, DemoMatchSource(), match_id)
    _assert_problem(
        await app_client.get(_listing(match_id), params={"participantId": 1}),
        409,
        "DECISIONS_NOT_PREPARED",
    )
    # extraída: 200
    await extract_decisions(session_factory, match_id, scope_path=_NO_SCOPE_FILE)
    ok = await app_client.get(_listing(match_id), params={"participantId": 1})
    assert ok.status_code == 200 and len(ok.json()["items"]) == 1


@pytest.mark.parametrize("_demo_match", ["DEMO-0001"], indirect=True)
async def test_extracted_with_zero_points_is_200_empty_not_409(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await _pipeline(session_factory, tmp_path, "DEMO-0001")  # só compra um componente
    response = await app_client.get(_listing("DEMO-0001"), params={"participantId": 1})
    assert response.status_code == 200
    assert response.json() == {"items": [], "nextCursor": None}


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_unknown_resources_are_404_and_bad_parameters_are_422(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    await _pipeline(session_factory, tmp_path, "DEMO-0002")
    _assert_problem(
        await app_client.get(_listing("DEMO-9999"), params={"participantId": 1}),
        404,
        "RESOURCE_NOT_FOUND",
    )
    _assert_problem(  # participante fora da partida (existem 1 e 6)
        await app_client.get(_listing("DEMO-0002"), params={"participantId": 9}),
        404,
        "RESOURCE_NOT_FOUND",
    )
    _assert_problem(
        await app_client.get(f"/api/v1/decisions/{uuid.uuid4()}"), 404, "RESOURCE_NOT_FOUND"
    )
    _assert_problem(await app_client.get("/api/v1/decisions/nao-e-uuid"), 422, "VALIDATION_ERROR")
    _assert_problem(  # matchId fora do padrão
        await app_client.get(_listing("abc"), params={"participantId": 1}), 422, "VALIDATION_ERROR"
    )
    for params in (
        {},  # participantId obrigatório
        {"participantId": 0},
        {"participantId": 11},
        {"participantId": "x"},
        {"participantId": 1, "limit": 0},
        {"participantId": 1, "limit": 101},
        {"participantId": 1, "limit": "x"},
    ):
        _assert_problem(
            await app_client.get(_listing("DEMO-0002"), params=params), 422, "VALIDATION_ERROR"
        )


def _b64(payload: Any) -> str:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_tampered_or_foreign_cursors_are_rejected_safely(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    match_id = "DEMO-0002"
    await _pipeline(
        session_factory,
        tmp_path,
        match_id,
        [_purchase(match_id, 4 + n, 480_000, 100010) for n in range(4)],
    )
    first = (
        await app_client.get(_listing(match_id), params={"participantId": 1, "limit": 2})
    ).json()
    valid = first["nextCursor"]
    assert valid
    decoded = json.loads(base64.urlsafe_b64decode(valid + "=" * (-len(valid) % 4)))
    good = {"participantId": 1, "limit": 2}

    bad_cursors = {
        "lixo": "!!!não-é-base64!!!",
        "base64 de não-JSON": _b64(b"isto nao e json"),
        "JSON não objeto": _b64([1, 2, 3]),
        "chaves faltando": _b64({"v": 1}),
        "chave extra": _b64({**decoded, "extra": 1}),
        "versão errada": _b64({**decoded, "v": 2}),
        "participante como texto": _b64({**decoded, "p": "1"}),
        "timestamp bool": _b64({**decoded, "t": True}),
        "timestamp negativo": _b64({**decoded, "t": -1}),
        "extração não-UUID": _b64({**decoded, "x": "nao-uuid"}),
        "ponto não-UUID": _b64({**decoded, "i": 7}),
        "extração inexistente": _b64({**decoded, "x": str(uuid.uuid4())}),
        "de outra partida": _b64({**decoded, "m": "DEMO-0008"}),
        "de outro participante": _b64({**decoded, "p": 6}),
        "gigante": "A" * 600,
    }
    for name, cursor in bad_cursors.items():
        response = await app_client.get(_listing(match_id), params={**good, "cursor": cursor})
        _assert_problem(response, 422, "INVALID_CURSOR", echoed=cursor[:40])
        assert response.status_code == 422, name

    # cursor válido usado com outro participante ou outra partida → 422 seguro
    _assert_problem(
        await app_client.get(_listing(match_id), params={"participantId": 6, "cursor": valid}),
        422,
        "INVALID_CURSOR",
        echoed=valid,
    )
    other = await app_client.get(
        _listing("DEMO-0008"), params={"participantId": 1, "cursor": valid}
    )
    assert other.status_code in (404, 422)  # partida inexistente/outra: nunca 200 nem eco
    assert valid not in other.text
    # e o cursor legítimo continua funcionando
    assert (
        await app_client.get(_listing(match_id), params={**good, "cursor": valid})
    ).status_code == 200


# ---------------------------------------------------------------------------
# Restrição pedagógica: nada do alvo na resposta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_target_related_reasons_are_neutral_and_context_reasons_stay_literal(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    match_id = "DEMO-0002"
    undo = TimelineEvent(
        match_id, 4, 473_000, "ITEM_UNDO", {"participantId": 1, "beforeId": 100010, "afterId": 0}
    )
    extra = [
        undo,
        _purchase(match_id, 5, 474_000, 999_999),
        _purchase(match_id, 6, 475_000, 100010),
    ]
    await _pipeline(session_factory, tmp_path, match_id, extra)
    items = {i["eventSourceIndex"]: i for i in await _all_pages(app_client, match_id, 1, 100)}
    assert set(items) == {3, 5, 6}
    # compra desfeita e item desconhecido: o motivo revelaria o resultado/a classe
    assert items[3]["eligible"] is False and items[3]["reasonCodes"] == ["TARGET_NOT_ELIGIBLE"]
    assert items[5]["eligible"] is False and items[5]["reasonCodes"] == ["TARGET_NOT_ELIGIBLE"]
    for index, forbidden in (
        (3, ("100010", "REVERT", "UNDO")),
        (5, ("999999", "UNKNOWN", "NOT_CLASSIFIABLE", "UNCLASSIFIED")),
    ):
        detail = await app_client.get(f"/api/v1/decisions/{items[index]['decisionId']}")
        assert detail.status_code == 200
        _assert_pre_decision_only(detail.json())
        assert detail.json()["reasons"] == [{"code": "TARGET_NOT_ELIGIBLE", "subReasons": []}]
        for word in forbidden:
            assert word not in detail.text.upper(), (index, word)
    # motivo de CONTEXTO preserva os sub-motivos literais
    context = (await app_client.get(f"/api/v1/decisions/{items[6]['decisionId']}")).json()
    [reason] = context["reasons"]
    assert reason["code"] == "INVENTORY_UNCERTAIN"
    assert reason["subReasons"] == ["ITEM_UNKNOWN", "OWNED_ITEM_UNCLASSIFIABLE"]
    # inventário, não alvo; item fora do catálogo fixado: nome null (nunca inventado)
    assert {"itemId": 999_999, "quantity": 1, "name": None} in context["inventory"]["items"]
    _assert_pre_decision_only(context)


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_no_response_carries_a_subject_hash(
    app_client, session_factory, tmp_path, _demo_match
) -> None:
    match_id = "DEMO-0002"
    await _pipeline(session_factory, tmp_path, match_id)
    async with unit_of_work(session_factory) as session:
        participants = await SqlAlchemyIngestionRepository(session).list_participants(match_id)
    hashes = [p.subject_hash for p in participants]
    assert hashes and all(len(h) >= 16 for h in hashes)
    listing = await app_client.get(_listing(match_id), params={"participantId": 1})
    [item] = listing.json()["items"]
    detail = await app_client.get(f"/api/v1/decisions/{item['decisionId']}")
    for response in (listing, detail):
        assert not any(h in response.text for h in hashes)
        assert "subject" not in response.text.lower()


# ---------------------------------------------------------------------------
# GET nunca reconstrói, extrai, abre fonte nem grava
# ---------------------------------------------------------------------------


async def _row_counts(session_factory, match_id: str) -> dict[str, int]:
    tables = (
        "decision_extractions",
        "decision_points",
        "decision_states",
        "timeline_events",
        "match_reconstructions",
        "participants",
    )
    counts: dict[str, int] = {}
    async with unit_of_work(session_factory) as session:
        for table in tables:
            result = await session.execute(
                text(f"SELECT count(*) FROM {table} WHERE match_id = :m"), {"m": match_id}
            )
            counts[table] = result.scalar_one()
        jobs = await session.execute(
            text("SELECT count(*) FROM import_jobs WHERE requested_match_id = :m"), {"m": match_id}
        )
        counts["import_jobs"] = jobs.scalar_one()
    return counts


@pytest.mark.parametrize("_demo_match", ["DEMO-0002"], indirect=True)
async def test_get_never_reconstructs_extracts_opens_source_or_writes(
    app_client, session_factory, tmp_path, _demo_match, monkeypatch
) -> None:
    match_id = "DEMO-0002"
    await _pipeline(session_factory, tmp_path, match_id)
    before = await _row_counts(session_factory, match_id)

    async def _forbidden(*args, **kwargs):
        raise AssertionError("a leitura não pode reconstruir, extrair nem abrir a fonte")

    monkeypatch.setattr(DemoMatchSource, "load_match", _forbidden)
    monkeypatch.setattr(timeline_application, "reconstruct_match", _forbidden)
    monkeypatch.setattr(decisions_application, "extract_decisions", _forbidden)

    listing = await app_client.get(_listing(match_id), params={"participantId": 1})
    [item] = listing.json()["items"]
    assert (await app_client.get(f"/api/v1/decisions/{item['decisionId']}")).status_code == 200
    await app_client.get(_listing(match_id), params={"participantId": 9})  # 404
    await app_client.get(_listing(match_id), params={"participantId": 1, "cursor": "x"})  # 422
    await app_client.get(_listing("DEMO-9999"), params={"participantId": 1})  # 404
    assert await _row_counts(session_factory, match_id) == before


async def test_read_transaction_is_read_only_at_the_database(session_factory) -> None:
    """Além de as rotas não chamarem escrita, a transação é READ ONLY: o próprio
    PostgreSQL rejeita qualquer INSERT/UPDATE/DELETE feito por esse caminho."""
    with pytest.raises(DBAPIError):
        async with decisions_queries._read_only(session_factory) as session:
            await session.execute(
                text("DELETE FROM decision_points WHERE match_id = 'DEMO-NEVER-EXISTS'")
            )
