"""Consumo de `.private/riot-account.json` (CC-105). Erros nunca ecoam os
valores lidos do arquivo — só nomes de campo e valores esperados."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from riftlab.ingestion.infrastructure.riot_account import (
    ALLOWED_PLATFORM,
    ALLOWED_REGIONAL_ROUTING,
    RiotAccountConfigError,
    load_riot_account,
)


def _write_account(tmp_path: Path, **overrides: object) -> Path:
    defaults: dict[str, object] = {
        "gameName": "ExemploSintetico",
        "tagLine": "BR1",
        "platform": ALLOWED_PLATFORM,
        "regionalRouting": ALLOWED_REGIONAL_ROUTING,
    }
    defaults.update(overrides)
    path = tmp_path / "riot-account.json"
    path.write_text(json.dumps(defaults), encoding="utf-8")
    return path


def test_load_riot_account_success(tmp_path: Path) -> None:
    account = load_riot_account(_write_account(tmp_path))

    assert account.game_name == "ExemploSintetico"
    assert account.tag_line == "BR1"
    assert account.platform == "BR1"
    assert account.regional_routing == "AMERICAS"


def test_load_riot_account_accepts_unicode_and_special_characters(tmp_path: Path) -> None:
    path = _write_account(tmp_path, gameName="Nomeção Ç é 名前", tagLine="BR-1")

    account = load_riot_account(path)

    assert account.game_name == "Nomeção Ç é 名前"
    assert account.tag_line == "BR-1"


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(RiotAccountConfigError):
        load_riot_account(tmp_path / "does-not-exist.json")


def test_malformed_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "riot-account.json"
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(RiotAccountConfigError):
        load_riot_account(path)


def test_non_object_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "riot-account.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(RiotAccountConfigError):
        load_riot_account(path)


@pytest.mark.parametrize("missing_field", ["gameName", "tagLine", "platform", "regionalRouting"])
def test_missing_required_field_raises(tmp_path: Path, missing_field: str) -> None:
    defaults: dict[str, object] = {
        "gameName": "X",
        "tagLine": "Y",
        "platform": ALLOWED_PLATFORM,
        "regionalRouting": ALLOWED_REGIONAL_ROUTING,
    }
    del defaults[missing_field]
    path = tmp_path / "riot-account.json"
    path.write_text(json.dumps(defaults), encoding="utf-8")

    with pytest.raises(RiotAccountConfigError) as excinfo:
        load_riot_account(path)
    assert missing_field in str(excinfo.value)


def test_platform_outside_scope_is_rejected_not_coerced(tmp_path: Path) -> None:
    path = _write_account(tmp_path, platform="NA1")

    with pytest.raises(RiotAccountConfigError):
        load_riot_account(path)


def test_regional_routing_outside_scope_is_rejected_not_coerced(tmp_path: Path) -> None:
    path = _write_account(tmp_path, regionalRouting="EUROPE")

    with pytest.raises(RiotAccountConfigError):
        load_riot_account(path)


def test_platform_and_regional_routing_are_normalized_case_insensitively(tmp_path: Path) -> None:
    # Bug real: a comparação original era sensível a maiúsculas/minúsculas e
    # rejeitava a conta real do usuário só por causa da capitalização do
    # arquivo. platform/regionalRouting nunca são segredo — só um valor
    # categórico — então normalizar (maiúsculas, sem espaço) é seguro.
    path = _write_account(tmp_path, platform="br1", regionalRouting="  americas  ")

    account = load_riot_account(path)

    assert account.platform == "BR1"
    assert account.regional_routing == "AMERICAS"


def test_empty_game_name_is_rejected(tmp_path: Path) -> None:
    path = _write_account(tmp_path, gameName="   ")

    with pytest.raises(RiotAccountConfigError):
        load_riot_account(path)


def test_error_messages_never_echo_the_configured_values(tmp_path: Path) -> None:
    path = _write_account(tmp_path, gameName="SegredoDoUsuario", platform="EUW1")

    with pytest.raises(RiotAccountConfigError) as excinfo:
        load_riot_account(path)

    assert "SegredoDoUsuario" not in str(excinfo.value)
    assert "EUW1" not in str(excinfo.value)
