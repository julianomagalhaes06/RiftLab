"""Blocos da coleta (CC-206): lista permitida de sementes, manifesto durável e filtros."""

from __future__ import annotations

import json
import os

import pytest

from riftlab.collection.filters import (
    FilterReason,
    MatchFilterConfig,
    evaluate_match_document,
    evaluate_match_id,
)
from riftlab.collection.manifest import (
    CollectionManifest,
    ManifestError,
    ManifestLockedError,
    SeedCursor,
)
from riftlab.collection.seeds import Seed, SeedsFileError, load_seeds
from support.riot_stub import real_pair

_ACCOUNT = {
    "gameName": "NomeSinteticoSecreto",
    "tagLine": "TAG9",
    "platform": "BR1",
    "regionalRouting": "americas",
}


def _write_account(directory, name="conta.json", **overrides) -> str:
    (directory / name).write_text(json.dumps({**_ACCOUNT, **overrides}))
    return name


def _seeds_file(directory, body: str):
    path = directory / "seeds.yaml"
    path.write_text(body)
    return path


def _valid_body(account: str, label: str = "seed-01", consent: str = "true") -> str:
    return (
        "schemaVersion: v1\nseeds:\n"
        f"  - label: {label}\n    accountFile: {account}\n    consent: {consent}\n"
    )


# ---------------------------------------------------------------------------
# sementes
# ---------------------------------------------------------------------------


def test_seeds_load_with_relative_account_path_and_never_repr_the_riot_id(tmp_path) -> None:
    account = _write_account(tmp_path)

    [seed] = load_seeds(_seeds_file(tmp_path, _valid_body(account)))

    assert seed.label == "seed-01" and seed.account.game_name == _ACCOUNT["gameName"]
    assert _ACCOUNT["gameName"] not in repr(seed) and _ACCOUNT["tagLine"] not in repr(seed)
    assert isinstance(seed, Seed)


# YAML 1.1 lê `yes`/`on` como o booleano true (o valor SEMÂNTICO é o que conta); texto entre
# aspas, número, null e false nunca valem como consentimento.
@pytest.mark.parametrize("consent", ["false", "'true'", "'yes'", "1", "null", "''"])
def test_consent_must_be_the_boolean_true(tmp_path, consent) -> None:
    account = _write_account(tmp_path)

    with pytest.raises(SeedsFileError, match="consent deve ser true"):
        load_seeds(_seeds_file(tmp_path, _valid_body(account, consent=consent)))


def test_missing_consent_is_rejected(tmp_path) -> None:
    account = _write_account(tmp_path)
    body = f"schemaVersion: v1\nseeds:\n  - label: seed-01\n    accountFile: {account}\n"

    with pytest.raises(SeedsFileError, match="consent deve ser true"):
        load_seeds(_seeds_file(tmp_path, body))


def test_inline_account_data_is_rejected_and_never_echoed(tmp_path) -> None:
    account = _write_account(tmp_path)
    body = _valid_body(account) + "    gameName: NomeSinteticoSecreto\n"

    with pytest.raises(SeedsFileError) as excinfo:
        load_seeds(_seeds_file(tmp_path, body))

    assert "gameName" in str(excinfo.value)  # o nome do CAMPO
    assert "NomeSinteticoSecreto" not in str(excinfo.value)  # nunca o valor


@pytest.mark.parametrize("label", ["Seed-01", "seed_01", "-seed", "s" * 40, "seed 01", ""])
def test_label_must_be_a_stable_pseudonym(tmp_path, label) -> None:
    account = _write_account(tmp_path)

    with pytest.raises(SeedsFileError, match="label"):
        load_seeds(_seeds_file(tmp_path, _valid_body(account, label=f"'{label}'")))


def test_duplicate_labels_and_unknown_top_level_keys_are_rejected(tmp_path) -> None:
    account = _write_account(tmp_path)
    entry = f"  - label: seed-01\n    accountFile: {account}\n    consent: true\n"

    with pytest.raises(SeedsFileError, match="repetido"):
        load_seeds(_seeds_file(tmp_path, "schemaVersion: v1\nseeds:\n" + entry + entry))
    with pytest.raises(SeedsFileError, match="desconhecida"):
        load_seeds(_seeds_file(tmp_path, "schemaVersion: v1\ndiscover: leagues\nseeds:\n" + entry))


def test_missing_account_file_wrong_platform_and_bad_schema_are_rejected(tmp_path) -> None:
    with pytest.raises(SeedsFileError, match="conta inválida"):
        load_seeds(_seeds_file(tmp_path, _valid_body("nao-existe.json")))

    account = _write_account(tmp_path, "na.json", platform="NA1")
    with pytest.raises(SeedsFileError, match="conta inválida"):
        load_seeds(_seeds_file(tmp_path, _valid_body(account)))

    with pytest.raises(SeedsFileError, match="schemaVersion"):
        load_seeds(_seeds_file(tmp_path, "schemaVersion: v9\nseeds: []\n"))
    with pytest.raises(SeedsFileError, match="não encontrado"):
        load_seeds(tmp_path / "nao-existe.yaml")


# ---------------------------------------------------------------------------
# manifesto
# ---------------------------------------------------------------------------


def test_manifest_from_earlier_rounds_with_a_positional_cursor_still_loads_and_drops_it(
    tmp_path,
) -> None:
    """Manifestos das Rodadas 1 a 2b guardavam `nextStart`. Continuam legíveis (a posição é
    ignorada de propósito) e a primeira gravação a remove."""
    legacy = {
        "schemaVersion": 1,
        "requestsUsedTotal": 16,
        "cooldownUntil": None,
        "authBlockedAt": None,
        "seeds": {"seed-03": {"nextStart": 37, "passesCompleted": 0, "exhausted": False}},
        "matches": {},
        "excluded": {},
        "transientFailures": {},
        "runs": [],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(legacy), encoding="utf-8")

    manifest = CollectionManifest(tmp_path)

    assert manifest.cursor("seed-03") == SeedCursor(passes_completed=0, exhausted=False)
    manifest.set_cursor("seed-03", manifest.cursor("seed-03"))
    manifest.save()
    saved = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert saved["seeds"]["seed-03"] == {"passesCompleted": 0, "exhausted": False}


def test_manifest_round_trips_state_and_leaves_no_temp_files(tmp_path) -> None:
    from datetime import UTC, datetime

    manifest = CollectionManifest(tmp_path)
    manifest.mark_fetched("BR1_1", seed="seed-01", patch="16.17", run_id="r1", at=datetime.now(UTC))
    manifest.mark_excluded("BR1_2", reason="QUEUE_OUT_OF_SCOPE", seed="seed-01", run_id="r1")
    manifest.set_cursor("seed-01", SeedCursor(passes_completed=2, exhausted=False))
    manifest.add_requests(5)
    manifest.save()

    reloaded = CollectionManifest(tmp_path)

    assert reloaded.status_of("BR1_1") == "FETCHED" and reloaded.status_of("BR1_2") == "EXCLUDED"
    assert reloaded.status_of("BR1_3") is None
    assert reloaded.cursor("seed-01") == SeedCursor(2, False)
    assert reloaded.requests_used_total == 5
    assert reloaded.excluded_by_reason() == {"QUEUE_OUT_OF_SCOPE": 1}
    assert not [name for name in os.listdir(tmp_path) if ".tmp-" in name]


def test_corrupted_or_unknown_manifest_is_never_overwritten(tmp_path) -> None:
    (tmp_path / "manifest.json").write_text("{isto não é json")
    with pytest.raises(ManifestError, match="ilegível"):
        CollectionManifest(tmp_path)
    assert (tmp_path / "manifest.json").read_text() == "{isto não é json"  # intacto

    (tmp_path / "manifest.json").write_text(json.dumps({"schemaVersion": 99}))
    with pytest.raises(ManifestError, match="schemaVersion"):
        CollectionManifest(tmp_path)

    (tmp_path / "manifest.json").write_text(json.dumps({"schemaVersion": 1, "matches": []}))
    with pytest.raises(ManifestError, match="inconsistente"):
        CollectionManifest(tmp_path)


def test_only_one_collection_at_a_time_per_manifest(tmp_path) -> None:
    first, second = CollectionManifest(tmp_path), CollectionManifest(tmp_path)

    with first.exclusive():
        with pytest.raises(ManifestLockedError):
            with second.exclusive():
                pytest.fail("a segunda coleta não pode entrar")
    with second.exclusive():  # liberado depois
        pass


def test_transient_failures_are_counted_per_match_and_cleared_on_success(tmp_path) -> None:
    from datetime import UTC, datetime

    manifest = CollectionManifest(tmp_path)
    assert manifest.record_transient_failure("BR1_1") == 1
    assert manifest.record_transient_failure("BR1_1") == 2
    manifest.mark_fetched("BR1_1", seed="s", patch="16.17", run_id="r", at=datetime.now(UTC))
    assert manifest.record_transient_failure("BR1_1") == 1


# ---------------------------------------------------------------------------
# filtros
# ---------------------------------------------------------------------------

_CONFIG = MatchFilterConfig(queue=420, map_id=11, patches=frozenset({"16.16", "16.17"}))
_PUUID = "puuid-semente"


def _body(**overrides) -> bytes:
    match_doc, _ = real_pair("BR1_1", seed_puuid=_PUUID, **overrides)
    return json.dumps(match_doc).encode()


def test_valid_match_is_accepted_and_reports_its_patch() -> None:
    result = evaluate_match_document(_body(patch="16.16"), _CONFIG, _PUUID)

    assert result.accepted and result.patch == "16.16"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"queue": 440}, FilterReason.QUEUE_OUT_OF_SCOPE),
        ({"map_id": 12}, FilterReason.MAP_OUT_OF_SCOPE),
        ({"result": "Abandoned"}, FilterReason.NOT_GAME_COMPLETE),
        ({"patch": "16.20"}, FilterReason.PATCH_OUT_OF_SCOPE),
    ],
)
def test_each_filter_has_its_own_reason(overrides, reason) -> None:
    result = evaluate_match_document(_body(**overrides), _CONFIG, _PUUID)

    assert result.reason is reason and result.patch is None


def test_first_failing_filter_wins_in_the_documented_order() -> None:
    result = evaluate_match_document(_body(queue=440, map_id=12, patch="1.1"), _CONFIG, _PUUID)

    assert result.reason is FilterReason.QUEUE_OUT_OF_SCOPE


def test_seed_must_be_a_participant_of_the_match() -> None:
    result = evaluate_match_document(_body(), _CONFIG, "puuid-de-outra-pessoa")

    assert result.reason is FilterReason.SEED_NOT_IN_MATCH


@pytest.mark.parametrize(
    "body",
    [b"nao e json", b"[]", b"{}", b'{"info": {}, "metadata": {}}', b'{"info": 1, "metadata": 2}'],
)
def test_malformed_documents_are_a_counted_loss_not_an_exception(body) -> None:
    result = evaluate_match_document(body, _CONFIG, _PUUID)

    assert result.reason in (FilterReason.MATCH_DOCUMENT_INVALID, FilterReason.QUEUE_OUT_OF_SCOPE)
    assert not result.accepted


def test_bad_game_version_is_invalid_not_a_crash() -> None:
    match_doc, _ = real_pair("BR1_1", seed_puuid=_PUUID)
    match_doc["info"]["gameVersion"] = "sem-ponto"

    result = evaluate_match_document(json.dumps(match_doc).encode(), _CONFIG, _PUUID)

    assert result.reason is FilterReason.MATCH_DOCUMENT_INVALID


@pytest.mark.parametrize(
    ("match_id", "loss"),
    [
        ("BR1_3120345678", None),
        ("NA1_5", FilterReason.PLATFORM_OUT_OF_SCOPE),
        ("LA1_9", FilterReason.PLATFORM_OUT_OF_SCOPE),
        ("BR1_", FilterReason.PLATFORM_OUT_OF_SCOPE),
        ("../BR1_1", FilterReason.PLATFORM_OUT_OF_SCOPE),
    ],
)
def test_foreign_platform_is_filtered_before_any_request(match_id, loss) -> None:
    assert evaluate_match_id(match_id) is loss


def test_the_documented_example_seeds_file_is_accepted_by_the_loader(tmp_path) -> None:
    """O exemplo público (configs/collection-seeds.example.yaml) não pode divergir do
    formato que o carregador realmente aceita."""
    from pathlib import Path

    here = Path(__file__).resolve()
    candidates = [
        root / "configs" / "collection-seeds.example.yaml"
        for root in (here.parents[2], here.parents[3])
    ]  # container (/app/configs) ou raiz do repositório (host)
    example = next((path for path in candidates if path.is_file()), None)
    assert example is not None, "configs/collection-seeds.example.yaml não encontrado"
    (tmp_path / "accounts").mkdir()
    for name in ("seed-01.json", "seed-02.json"):
        (tmp_path / "accounts" / name).write_text(json.dumps(_ACCOUNT))
    (tmp_path / "seeds.yaml").write_text(example.read_text(encoding="utf-8"))

    seeds = load_seeds(tmp_path / "seeds.yaml")

    assert [seed.label for seed in seeds] == ["seed-01", "seed-02"]
    assert "gameName" not in example.read_text(encoding="utf-8").split("schemaVersion")[1]


def test_filters_report_the_match_date_even_for_matches_they_reject() -> None:
    accepted = evaluate_match_document(_body(start_ms=1_784_073_600_000), _CONFIG, _PUUID)
    rejected = evaluate_match_document(
        _body(start_ms=1_780_000_000_000, patch="16.5"), _CONFIG, _PUUID
    )

    assert accepted.accepted and accepted.game_date == "2026-07-15"
    assert rejected.reason is FilterReason.PATCH_OUT_OF_SCOPE and rejected.game_date == "2026-05-28"


@pytest.mark.parametrize("timestamp", [None, "ontem", -5, True, 10**30])
def test_a_missing_or_absurd_timestamp_never_breaks_the_filter(timestamp) -> None:
    match_doc, _ = real_pair("BR1_1", seed_puuid=_PUUID)
    match_doc["info"]["gameStartTimestamp"] = timestamp

    result = evaluate_match_document(json.dumps(match_doc).encode(), _CONFIG, _PUUID)

    assert result.accepted and result.game_date is None


def test_manifest_summarises_game_dates_by_patch_and_by_exclusion_reason(tmp_path) -> None:
    from datetime import UTC, datetime

    manifest = CollectionManifest(tmp_path)
    at = datetime.now(UTC)
    manifest.mark_fetched(
        "BR1_1", seed="s", patch="16.17", run_id="r", at=at, game_date="2026-08-01"
    )
    manifest.mark_fetched(
        "BR1_2", seed="s", patch="16.17", run_id="r", at=at, game_date="2026-09-03"
    )
    manifest.mark_fetched("BR1_3", seed="s", patch="16.16", run_id="r", at=at, game_date=None)
    manifest.mark_excluded(
        "BR1_4", reason="PATCH_OUT_OF_SCOPE", seed="s", run_id="r", game_date="2026-07-16"
    )
    manifest.mark_excluded("BR1_5", reason="RIOT_NOT_FOUND", seed="s", run_id="r")

    assert manifest.date_ranges() == {
        "fetchedByPatch": {"16.17": {"min": "2026-08-01", "max": "2026-09-03"}},
        "excludedByReason": {"PATCH_OUT_OF_SCOPE": {"min": "2026-07-16", "max": "2026-07-16"}},
    }
