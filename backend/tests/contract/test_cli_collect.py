"""CLI `cc collect ...` (CC-206): recusas seguras ANTES de qualquer requisição, códigos de
saída por causa de parada, retomada e ausência de dado pessoal na saída. Servidor Riot
falso injetado pelo gancho `_http_client`; ambiente sempre isolado do .env e das contas
reais do usuário."""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from riftlab.cli.main import app
from riftlab.config import get_settings
from riftlab.ingestion.application import raw_pair_available
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from support.collection_harness import NAME_A, SEED_A_PUUID, TAG_A
from support.riot_stub import API_KEY, HASH_SECRET, RiotStub, status_response

runner = CliRunner()
SEEDS_YAML = (
    "schemaVersion: v1\nseeds:\n  - label: seed-01\n    accountFile: conta.json\n"
    "    consent: {consent}\n"
)
SCOPE_YAML = (
    "schemaVersion: v1\nfrozenAt: '2026-09-19'\nqueue: 420\nmap: 11\nrole: UTILITY\n"
    "championIds: [89]\npatches:\n  - gamePatch: '16.17'\n  - gamePatch: '16.16'\n"
)


@pytest.fixture(autouse=True)
def _isolated_environment(tmp_path, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setattr("riftlab.cli.env_file.DEFAULT_ENV_FILE", tmp_path / "sem-env")
    for name in ("RIFTLAB_RIOT_API_KEY", "RIFTLAB_SUBJECT_HASH_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RIFTLAB_RAW_STORAGE_DIR", str(tmp_path / "raw"))
    yield
    get_settings.cache_clear()


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "scope.yaml").write_text(SCOPE_YAML)
    (tmp_path / "conta.json").write_text(
        json.dumps(
            {"gameName": NAME_A, "tagLine": TAG_A, "platform": "BR1", "regionalRouting": "americas"}
        )
    )
    (tmp_path / "seeds.yaml").write_text(SEEDS_YAML.format(consent="true"))
    return tmp_path


def _keys(monkeypatch) -> None:
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", API_KEY)
    monkeypatch.setenv("RIFTLAB_SUBJECT_HASH_SECRET", HASH_SECRET)


def _stub(monkeypatch, *match_ids: str, patch: str = "16.17") -> RiotStub:
    stub = RiotStub()
    stub.add_account(NAME_A, SEED_A_PUUID)
    for match_id in match_ids:
        stub.add_match(match_id, seed_puuid=SEED_A_PUUID, patch=patch)
    monkeypatch.setattr(
        "riftlab.cli.collect._http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(stub)),
    )
    return stub


def _run(workspace, *extra: str):
    return runner.invoke(
        app,
        [
            "collect",
            "run",
            "--seeds-file",
            str(workspace / "seeds.yaml"),
            "--scope",
            str(workspace / "scope.yaml"),
            *extra,
        ],
    )


def test_blocks_without_the_riot_key_and_touches_nothing(workspace, monkeypatch) -> None:
    stub = _stub(monkeypatch, "BR1_1")
    monkeypatch.setenv("RIFTLAB_SUBJECT_HASH_SECRET", HASH_SECRET)

    result = _run(workspace)

    assert result.exit_code == 1 and "RIFTLAB_RIOT_API_KEY" in result.output
    assert stub.calls == [] and not (workspace / "raw" / "collection").exists()
    assert HASH_SECRET not in result.output


def test_blocks_without_the_hash_secret_before_any_request_and_never_generates_one(
    workspace, monkeypatch
) -> None:
    stub = _stub(monkeypatch, "BR1_1")
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", API_KEY)

    result = _run(workspace)

    assert result.exit_code == 1 and "RIFTLAB_SUBJECT_HASH_SECRET" in result.output
    assert "nunca gera" in result.output
    assert stub.calls == [] and API_KEY not in result.output
    assert not (workspace / "raw" / "collection" / "manifest.json").exists()


def test_blocks_on_invalid_seeds_patches_and_dates(workspace, monkeypatch) -> None:
    _keys(monkeypatch)
    stub = _stub(monkeypatch, "BR1_1")
    (workspace / "seeds.yaml").write_text(SEEDS_YAML.format(consent="false"))
    without_consent = _run(workspace)
    assert without_consent.exit_code == 1 and "consent" in without_consent.output

    (workspace / "seeds.yaml").write_text(SEEDS_YAML.format(consent="true"))
    widened = _run(workspace, "--patches", "16.17,16.99")
    assert widened.exit_code == 1 and "estreitar" in widened.output
    bad_date = _run(workspace, "--since", "ontem")
    assert bad_date.exit_code == 1 and "AAAA-MM-DD" in bad_date.output
    reversed_window = _run(workspace, "--since", "2026-09-10", "--until", "2026-09-01")
    assert reversed_window.exit_code == 1 and "anterior" in reversed_window.output
    zero_ceiling = _run(workspace, "--max-requests", "0")
    assert zero_ceiling.exit_code == 1
    assert stub.calls == []  # nada foi buscado em nenhuma recusa


def test_collects_reports_and_leaks_no_personal_data(workspace, monkeypatch) -> None:
    _keys(monkeypatch)
    stub = _stub(monkeypatch, "BR1_1", "BR1_2")

    result = _run(workspace, "--json", "--patches", "16.17", "--since", "2026-01-01")

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["totals"]["fetched"] == 2 and data["run"]["stopReason"] == "COMPLETED"
    assert data["run"]["newValid"] == 2 and data["totals"]["fetchedByPatch"] == {"16.17": 2}
    raw = DurableRawMatchStore(workspace / "raw")
    assert raw_pair_available(raw, "BR1_1") and raw_pair_available(raw, "BR1_2")
    assert stub.list_params[0]["startTime"] == "1767225600"  # 2026-01-01 UTC
    manifest_text = (workspace / "raw" / "collection" / "manifest.json").read_text()
    for text in (result.output, manifest_text):
        for secret in (NAME_A, TAG_A, SEED_A_PUUID, API_KEY, HASH_SECRET):
            assert secret not in text


def test_401_exits_3_blocks_and_only_a_confirmed_key_swap_resumes(workspace, monkeypatch) -> None:
    _keys(monkeypatch)
    stub = _stub(monkeypatch, "BR1_1", "BR1_2")
    stub.fail_auth_after(0)

    first = _run(workspace)
    assert first.exit_code == 3 and "BLOQUEIO por credencial" in first.output
    calls = len(stub.calls)

    blocked = _run(workspace)
    assert blocked.exit_code == 3 and len(stub.calls) == calls  # recusou sem gastar requisição

    stub.fix_auth()
    resumed = _run(workspace, "--resume-after-auth-failure")
    assert resumed.exit_code == 0
    assert stub.count("match", "BR1_1") == 1 and "BLOQUEIO" not in resumed.output


def test_429_exits_4_and_the_cooldown_blocks_the_next_run(workspace, monkeypatch) -> None:
    _keys(monkeypatch)
    stub = _stub(monkeypatch, "BR1_1")
    stub.script("account", status_response(429, Retry_After="900"))

    first = _run(workspace, "--max-duration-seconds", "60")
    assert first.exit_code == 4 and "cooldown" in first.output
    calls = len(stub.calls)

    again = _run(workspace)
    assert again.exit_code == 4 and len(stub.calls) == calls


def test_ceiling_stop_exits_zero_and_report_command_reads_the_manifest_offline(
    workspace, monkeypatch
) -> None:
    _keys(monkeypatch)
    _stub(monkeypatch, "BR1_1", "BR1_2", "BR1_3")

    capped = _run(workspace, "--max-matches", "1")
    assert capped.exit_code == 0 and "parada=MAX_MATCHES" in capped.output

    monkeypatch.delenv("RIFTLAB_RIOT_API_KEY")  # o relatório não precisa de chave nem de rede
    report = runner.invoke(app, ["collect", "report", "--json"])
    assert report.exit_code == 0
    data = json.loads(report.output)
    assert data["totals"]["fetched"] == 1
    # sem posição: passada aberta; o progresso é o que já se conhece (custa 0 na próxima)
    assert data["resume"]["seeds"]["seed-01"] == {
        "passesCompleted": 0,
        "exhausted": False,
        "known": 1,
    }
    assert "nextStart" not in report.output
    text = runner.invoke(app, ["collect", "report"])
    assert "passada aberta" in text.output and "coletadas: 1" in text.output
    first_run = runner.invoke(app, ["collect", "report", "--run", "1"])
    assert "rodada" in first_run.output
    assert runner.invoke(app, ["collect", "report", "--run", "9"]).exit_code == 1


def test_report_without_manifest_is_blocked_not_a_crash(workspace) -> None:
    result = runner.invoke(app, ["collect", "report"])

    assert result.exit_code == 1 and "nenhum manifesto" in result.output
