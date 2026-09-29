"""CLI `cc catalog sync` (CC-202) contra PostgreSQL real: modo demo sem rede,
idempotência real via linha de comando, bloqueios seguros."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from riftlab.cli.main import app
from riftlab.config import get_settings

runner = CliRunner()

_CATALOG_ID_PATTERN = re.compile(r"catalogId=([0-9a-f-]{36})")


def setup_function() -> None:
    # get_settings() é @lru_cache — sem isso, Settings de um teste vazaria
    # para o próximo dentro do mesmo processo do pytest (mesma disciplina de
    # test_cli_riot_audit_sample.py, CC-105).
    get_settings.cache_clear()


def teardown_function() -> None:
    get_settings.cache_clear()


def test_catalog_sync_rejects_unknown_mode() -> None:
    result = runner.invoke(app, ["catalog", "sync", "--mode", "bogus", "--patch", "DEMO-1"])

    assert result.exit_code == 1
    assert "BLOCKED" in result.output


def test_catalog_sync_demo_mode_persists_and_is_idempotent_via_cli(_migrated_database) -> None:
    # _migrated_database (fixture de tests/integration/conftest.py, sync,
    # session-scoped) garante alembic upgrade head antes deste teste ser o
    # primeiro a tocar o banco real — este comando constrói sua própria
    # engine fora do fixture session_factory, então não herda essa garantia
    # por acidente de ordem de execução da suíte.
    first = runner.invoke(app, ["catalog", "sync", "--mode", "demo", "--patch", "DEMO-1"])
    assert first.exit_code == 0, first.output
    match_first = _CATALOG_ID_PATTERN.search(first.output)
    assert match_first is not None

    second = runner.invoke(app, ["catalog", "sync", "--mode", "demo", "--patch", "DEMO-1"])
    assert second.exit_code == 0, second.output
    match_second = _CATALOG_ID_PATTERN.search(second.output)
    assert match_second is not None

    assert match_first.group(1) == match_second.group(1)  # mesma identidade, idempotente


def test_catalog_sync_demo_mode_unknown_patch_blocks_safely() -> None:
    result = runner.invoke(
        app, ["catalog", "sync", "--mode", "demo", "--patch", "DEMO-999-DOES-NOT-EXIST"]
    )

    assert result.exit_code == 1
    assert "BLOCKED" in result.output


def test_catalog_sync_real_mode_missing_mapping_file_blocks(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            "catalog",
            "sync",
            "--mode",
            "real",
            "--patch",
            "16.17",
            "--mapping-file",
            str(tmp_path / "does-not-exist.yaml"),
        ],
    )

    assert result.exit_code == 1
    assert "BLOCKED" in result.output


def test_catalog_sync_real_mode_unmapped_patch_blocks_without_network(tmp_path) -> None:
    mapping_file = tmp_path / "catalog-mappings.yaml"
    mapping_file.write_text(
        "schemaVersion: v1\nmappingVersion: v1\n"
        'mappings: {"16.17": {"dataDragonVersion": "16.17.1"}}\n'
    )

    result = runner.invoke(
        app,
        [
            "catalog",
            "sync",
            "--mode",
            "real",
            "--patch",
            "13.1",
            "--mapping-file",
            str(mapping_file),
        ],
    )

    assert result.exit_code == 1
    assert "BLOCKED" in result.output
    assert "MISSING_CATALOG" in result.output


def test_sync_champions_for_a_patch_without_mapping_uses_no_network(
    _migrated_database, tmp_path, monkeypatch
) -> None:
    """CC-208: `cc catalog sync-champions` com patch fora do mapping termina sem rede
    (a busca real falharia o teste se fosse chamada)."""
    from riftlab.catalog.infrastructure import data_dragon_transport

    async def _no_network(*_args, **_kwargs):
        raise AssertionError("não deveria buscar o CDN")

    monkeypatch.setattr(
        data_dragon_transport.DataDragonTransport, "fetch_champion_catalog", _no_network
    )
    mapping = tmp_path / "catalog-mappings.yaml"
    mapping.write_text(
        "schemaVersion: v1\nmappingVersion: v1\n"
        'mappings:\n  "16.17": {dataDragonVersion: "16.17.1"}\n'
    )
    result = runner.invoke(
        app, ["catalog", "sync-champions", "--patch", "99.1", "--mapping-file", str(mapping)]
    )
    assert result.exit_code == 1
    assert "resultado=NO_MAPPING" in result.output


def test_sync_champions_without_mapping_file_is_blocked(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            "catalog",
            "sync-champions",
            "--patch",
            "16.17",
            "--mapping-file",
            str(tmp_path / "x.yaml"),
        ],
    )
    assert result.exit_code == 1 and "BLOCKED" in result.output
