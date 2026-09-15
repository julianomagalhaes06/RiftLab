"""Carregamento explícito do .env para a CLI (CC-105): pydantic-settings/
python-dotenv (sem shell-source), precedência real de variável de ambiente
sobre o arquivo, arquivo ausente cai para modo demo sem erro."""

from __future__ import annotations

from riftlab.cli.env_file import load_settings, resolve_env_file


def test_resolve_env_file_defaults_to_repo_root_not_backend() -> None:
    resolved = resolve_env_file(None)
    assert resolved.name == ".env"
    assert resolved.parent.name != "backend"


def test_resolve_env_file_honors_explicit_path(tmp_path) -> None:
    explicit = tmp_path / "custom.env"
    assert resolve_env_file(explicit) == explicit


def test_load_settings_reads_value_from_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RIFTLAB_RIOT_API_KEY", raising=False)
    env_file = tmp_path / "test.env"
    env_file.write_text("RIFTLAB_RIOT_API_KEY=SYNTHETIC-VALUE-FROM-DOTENV\n")

    settings = load_settings(env_file)

    assert settings.riot_api_key == "SYNTHETIC-VALUE-FROM-DOTENV"


def test_load_settings_missing_file_falls_back_to_real_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", "SYNTHETIC-FROM-ENV")
    missing = tmp_path / "does-not-exist.env"

    settings = load_settings(missing)

    assert settings.riot_api_key == "SYNTHETIC-FROM-ENV"


def test_load_settings_missing_file_and_no_env_var_stays_in_demo_mode(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("RIFTLAB_RIOT_API_KEY", raising=False)
    missing = tmp_path / "does-not-exist.env"

    settings = load_settings(missing)

    assert settings.riot_api_key is None
    assert settings.demo_mode is True


def test_real_environment_variable_takes_precedence_over_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RIFTLAB_RIOT_API_KEY", "SYNTHETIC-FROM-REAL-ENV")
    env_file = tmp_path / "test.env"
    env_file.write_text("RIFTLAB_RIOT_API_KEY=SYNTHETIC-FROM-DOTENV\n")

    settings = load_settings(env_file)

    assert settings.riot_api_key == "SYNTHETIC-FROM-REAL-ENV"
