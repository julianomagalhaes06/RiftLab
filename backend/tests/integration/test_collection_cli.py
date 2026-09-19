"""`cc collect ...` e `cc report ...` de ponta a ponta contra PostgreSQL real (CC-206):
coleta com servidor Riot falso -> enfileirar a importação a partir do raw local ->
importar/preparar -> relatórios agregados. Sem rede real: o catálogo da preparação é o
stub e o cliente HTTP da coleta é o servidor falso."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest_asyncio
from pipeline_helpers import (
    StubCatalogSource,
    purge_open_preparations,
    reset_match,
    stub_catalog_factory,
    unique_patch,
    write_prepare_configs,
)
from typer.testing import CliRunner

from riftlab.cli.main import app
from riftlab.collection.importing import drain_local
from riftlab.config import Settings, get_settings
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.infrastructure.subject_hash import compute_real_subject_hash
from support.collection_harness import NAME_A, SEED_A_PUUID, TAG_A
from support.riot_stub import API_KEY, HASH_SECRET, RiotStub

runner = CliRunner()


@pytest_asyncio.fixture(autouse=True)
async def _isolated_queues(session_factory):
    await purge_open_preparations(session_factory)
    yield
    await purge_open_preparations(session_factory)


async def _cli(*args: str):
    """A CLI chama asyncio.run: roda numa thread sem laço de eventos."""
    return await asyncio.to_thread(runner.invoke, app, list(args))


async def test_cli_collect_import_and_reports_end_to_end(
    session_factory, tmp_path, monkeypatch
) -> None:
    get_settings.cache_clear()
    patch = unique_patch()
    ids = [f"BR1_{uuid.uuid4().int % 10**10}" for _ in range(2)]
    config = write_prepare_configs(tmp_path, scope_patch=patch, mapped_patches=(patch,))
    (tmp_path / "conta.json").write_text(
        json.dumps(
            {"gameName": NAME_A, "tagLine": TAG_A, "platform": "BR1", "regionalRouting": "americas"}
        )
    )
    (tmp_path / "seeds.yaml").write_text(
        "schemaVersion: v1\nseeds:\n  - label: seed-01\n    accountFile: conta.json\n"
        "    consent: true\n"
    )
    monkeypatch.setattr("riftlab.cli.env_file.DEFAULT_ENV_FILE", tmp_path / "sem-env")
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", API_KEY)
    monkeypatch.setenv("RIFTLAB_SUBJECT_HASH_SECRET", HASH_SECRET)
    monkeypatch.setenv("RIFTLAB_RAW_STORAGE_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("RIFTLAB_SCOPE_PATH", config.scope_path)
    monkeypatch.setenv("RIFTLAB_CATALOG_MAPPINGS_PATH", config.catalog_mappings_path)
    stub = RiotStub()
    stub.add_account(NAME_A, SEED_A_PUUID)
    for match_id in ids:
        stub.add_match(match_id, seed_puuid=SEED_A_PUUID, patch=patch, purchases=((120_000, 3006),))
    monkeypatch.setattr(
        "riftlab.cli.collect._http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(stub)),
    )
    try:
        collected = await _cli(
            "collect", "run", "--seeds-file", str(tmp_path / "seeds.yaml"), "--json"
        )
        assert collected.exit_code == 0, collected.output
        assert json.loads(collected.output)["totals"]["fetched"] == 2
        calls = len(stub.calls)

        enqueued = await _cli("collect", "import", "--no-drain", "--json")
        assert enqueued.exit_code == 0, enqueued.output
        assert json.loads(enqueued.output) | {"drained": None} == {
            "collected": 2,
            "enqueued": 2,
            "alreadyRequested": 0,
            "rawIncomplete": 0,
            "drained": None,
        }
        again = await _cli("collect", "import", "--no-drain", "--json")
        assert json.loads(again.output)["alreadyRequested"] == 2  # idempotente

        settings = Settings().model_copy(update={"subject_hash_secret": HASH_SECRET})
        raw_store = DurableRawMatchStore(tmp_path / "raw")
        await drain_local(
            session_factory,
            raw_store,
            settings,
            catalog_source_factory=stub_catalog_factory(StubCatalogSource()),
        )
        assert len(stub.calls) == calls  # importar e preparar não buscou nada

        outputs = {}
        for name in ("reconstruction", "diversity", "integrity"):
            result = await _cli("report", name, "--only-collected", "--json")
            assert result.exit_code == 0, (name, result.output)
            outputs[name] = json.loads(result.output)
        assert outputs["reconstruction"]["preparation"] == {"READY": 2}
        assert outputs["reconstruction"]["points"]["total"] == 2
        assert outputs["diversity"]["distinctPlayers"] == 3
        assert outputs["integrity"]["ok"] is True and outputs["integrity"]["rawStoreChecked"]

        text = await _cli("report", "reconstruction", "--only-collected")
        assert "READY" in text.output and "pontos de decisão" in text.output
        forbidden = (
            NAME_A,
            SEED_A_PUUID,
            API_KEY,
            HASH_SECRET,
            compute_real_subject_hash(SEED_A_PUUID, HASH_SECRET),
            *ids,
        )
        for output in (*(json.dumps(v) for v in outputs.values()), text.output):
            for value in forbidden:
                assert value not in output

        # o par bruto de uma partida some do disco: a verificação acusa e a CLI sai com 1
        (tmp_path / "raw" / f"{ids[0]}.timeline.ref").unlink()
        broken = await _cli("report", "integrity", "--only-collected", "--json")
        assert broken.exit_code == 1
        assert json.loads(broken.output)["violations"]["RAW_PAIR_MISSING"] == 1
    finally:
        get_settings.cache_clear()
        for match_id in ids:
            await reset_match(session_factory, match_id)
