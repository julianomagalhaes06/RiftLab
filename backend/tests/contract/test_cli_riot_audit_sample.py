"""CLI `cc riot audit-sample` (CC-105): gate real de credencial/sementes/conta,
sem crashear e sem ecoar chave/valores da conta, mesmo quando existem de
verdade no ambiente/disco. Isola sempre os defaults de .env/conta para nunca
tocar os arquivos reais do usuário nem arriscar uma chamada de rede real."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from riftlab.cli.main import app
from riftlab.config import get_settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_defaults_and_settings_cache(tmp_path, monkeypatch):
    # get_settings() e @lru_cache; sem isso, a chave/ambiente de um teste
    # vazaria para o proximo dentro do mesmo processo do pytest.
    get_settings.cache_clear()

    # Nunca deixar os testes tocarem o .env/riot-account.json REAIS configurados
    # pelo usuario neste repositorio (existem de verdade nesta maquina) — sem
    # isso, um teste que nao passa --seeds/--account-file explicitamente
    # acabaria lendo a conta real e tentando uma descoberta de rede real.
    monkeypatch.setattr(
        "riftlab.ingestion.infrastructure.riot_account.DEFAULT_ACCOUNT_FILE",
        tmp_path / "isolated-default-riot-account.json",
    )
    monkeypatch.setattr("riftlab.cli.env_file.DEFAULT_ENV_FILE", tmp_path / "isolated-default.env")
    yield
    get_settings.cache_clear()


def _write_seeds(tmp_path, *match_ids: str, comment: str | None = None):
    seeds = tmp_path / "seeds.txt"
    lines = ([f"# {comment}"] if comment else []) + list(match_ids)
    seeds.write_text("\n".join(lines) + "\n")
    return seeds


def _write_account_file(tmp_path, **overrides: object):
    defaults: dict[str, object] = {
        "gameName": "ExemploSintetico",
        "tagLine": "BR1",
        "platform": "BR1",
        "regionalRouting": "AMERICAS",
    }
    defaults.update(overrides)
    path = tmp_path / "riot-account.json"
    path.write_text(json.dumps(defaults), encoding="utf-8")
    return path


def test_blocks_without_riot_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RIFTLAB_RIOT_API_KEY", raising=False)
    seeds = _write_seeds(tmp_path, "BR1_1000000001")

    result = runner.invoke(app, ["riot", "audit-sample", "--seeds", str(seeds)])

    assert result.exit_code == 1
    assert "BLOCKED" in result.output
    assert "RIFTLAB_RIOT_API_KEY" in result.output


def test_blocks_without_seeds_or_account_file(monkeypatch) -> None:
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", "RGAPI-fake-not-real")

    result = runner.invoke(app, ["riot", "audit-sample"])

    assert result.exit_code == 1
    assert "BLOCKED" in result.output


def test_blocks_when_seeds_file_has_no_match_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", "RGAPI-fake-not-real")
    seeds = _write_seeds(tmp_path, comment="nenhuma semente ainda")

    result = runner.invoke(app, ["riot", "audit-sample", "--seeds", str(seeds)])

    assert result.exit_code == 1
    assert "BLOCKED" in result.output


def test_never_echoes_the_api_key_even_when_blocked_for_other_reasons(monkeypatch) -> None:
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", "RGAPI-should-never-appear-in-output")

    result = runner.invoke(app, ["riot", "audit-sample"])

    assert "RGAPI-should-never-appear-in-output" not in result.output


def test_blocks_when_seeds_and_account_file_both_given(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", "RGAPI-fake-not-real")
    seeds = _write_seeds(tmp_path, "BR1_1000000001")
    account_file = _write_account_file(tmp_path)

    result = runner.invoke(
        app,
        [
            "riot",
            "audit-sample",
            "--seeds",
            str(seeds),
            "--account-file",
            str(account_file),
        ],
    )

    assert result.exit_code == 1
    assert "BLOCKED" in result.output
    assert "mutuamente exclusivos" in result.output


def test_blocks_when_explicit_account_file_does_not_exist(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", "RGAPI-fake-not-real")

    result = runner.invoke(
        app,
        ["riot", "audit-sample", "--account-file", str(tmp_path / "nao-existe.json")],
    )

    assert result.exit_code == 1
    assert "BLOCKED" in result.output


def test_blocks_when_account_file_malformed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", "RGAPI-fake-not-real")
    account_file = tmp_path / "riot-account.json"
    account_file.write_text("{not valid json", encoding="utf-8")

    result = runner.invoke(app, ["riot", "audit-sample", "--account-file", str(account_file)])

    assert result.exit_code == 1
    assert "BLOCKED" in result.output


def test_never_echoes_account_field_values_even_when_blocked(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", "RGAPI-fake-not-real")
    account_file = _write_account_file(
        tmp_path,
        gameName="SegredoDoUsuario",
        platform="EUW1",  # fora do recorte -> BLOCKED
    )

    result = runner.invoke(app, ["riot", "audit-sample", "--account-file", str(account_file)])

    assert result.exit_code == 1
    assert "BLOCKED" in result.output
    assert "SegredoDoUsuario" not in result.output


def test_blocks_when_request_budget_already_exhausted_by_previous_run(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", "RGAPI-fake-not-real")
    seeds = _write_seeds(tmp_path, "BR1_1000000001")
    out_dir = tmp_path / "raw"
    out_dir.mkdir()
    (out_dir / "manifest.json").write_text(
        json.dumps({"matches": {}, "excluded": {}, "budget": {"requestsUsedTotal": 200}})
    )

    result = runner.invoke(
        app,
        [
            "riot",
            "audit-sample",
            "--seeds",
            str(seeds),
            "--out-dir",
            str(out_dir),
            "--max-requests",
            "200",
        ],
    )

    assert result.exit_code == 1
    assert "BLOCKED" in result.output
    assert "orcamento" in result.output.lower() or "orçamento" in result.output.lower()
