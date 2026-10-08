"""Registro de regras empíricas (CC-207): carregamento estrito, proveniência, guarda de
versão e o pré-passo puro do R4w. Sem I/O de banco."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from riftlab.timeline.application import RECONSTRUCTION_ALGORITHM_VERSION as VERSION
from riftlab.timeline.domain import TimelineEvent
from riftlab.timeline.empirical_rules import (
    KNOWN_RULE_IDS,
    EmpiricalRules,
    EmpiricalRulesConfigError,
    attribute_system_purchases,
    load_empirical_rules,
    load_empirical_rules_or_disabled,
    parse_empirical_rules,
)


def _find_config() -> Path:
    """`configs/` fica em /app na imagem de teste e na raiz do repositório no host."""
    here = Path(__file__).resolve()
    for root in (here.parents[3], here.parents[4]):
        candidate = root / "configs" / "empirical-rules.yaml"
        if candidate.exists():
            return candidate
    raise AssertionError("configs/empirical-rules.yaml não encontrado")


CONFIG = _find_config()


def _document() -> dict:
    return copy.deepcopy(yaml.safe_load(CONFIG.read_text(encoding="utf-8")))


def _pending(document: dict, rule_id: str) -> dict:
    """Entrada PENDING pura, independente do estado atual do registro do repositório."""
    document["rules"][rule_id].update(
        status="PENDING",
        patchesValidated=[],
        contraRate=None,
        counterExamples=None,
        evidence=None,
        activatedInVersion=None,
    )
    return document["rules"][rule_id]


def _supported(document: dict, rule_id: str = "R5", patches: tuple[str, ...] = ("16.17",)) -> dict:
    document["rules"][rule_id].update(
        status="EMPIRICALLY_SUPPORTED",
        patchesValidated=list(patches),
        contraRate=0.0,
        counterExamples=0,
        evidence="validation/sprint-2/CC-207/50-confirmation-result.json",
        activatedInVersion=VERSION,
    )
    return document


# ---------------------------------------------------------------------------
# o registro versionado do repositório
# ---------------------------------------------------------------------------


def test_repository_registry_declares_every_rule_and_starts_all_pending() -> None:
    rules = load_empirical_rules(CONFIG, reconstruction_version=VERSION)
    assert set(rules.entries) == set(KNOWN_RULE_IDS)
    assert all(entry.status == "PENDING" for entry in rules.entries.values()) or all(
        entry.activated_in_version == VERSION
        for entry in rules.entries.values()
        if entry.status == "EMPIRICALLY_SUPPORTED"
    )
    assert rules.system_item_ids >= {1202, 2001, 2002}
    assert 3866 not in rules.system_item_ids and 3867 not in rules.system_item_ids  # ocupam slot


def test_repository_registry_releases_only_the_rules_confirmed_for_16_19() -> None:
    """Estado liberado pelas confirmações únicas CC-316 (CC-322: R1, R2, R2i, R4s, R4w, R5) e
    CC-324 (R7b, R7m, R7x, R8, R8t), só em 16.19; R6 (H6 REJECT) e qualquer outro patch
    continuam com o comportamento da v6."""
    rules = load_empirical_rules(CONFIG, reconstruction_version=VERSION)
    assert rules.enabled_for("16.19") == frozenset(
        {"R1", "R2", "R2i", "R4s", "R4w", "R5", "R7b", "R7m", "R7x", "R8", "R8t"}
    )
    for patch in ("16.16", "16.17", "16.18", "16.20"):
        assert rules.enabled_for(patch) == frozenset()
    assert rules.entries["R6"].status == "PENDING"
    for rule_id in ("R1", "R2", "R2i", "R4s", "R4w", "R5"):
        entry = rules.entries[rule_id]
        assert entry.patches_validated == frozenset({"16.19"})
        assert entry.evidence and "CC-322" in entry.evidence
    for rule_id in ("R7b", "R7m", "R7x", "R8", "R8t"):
        entry = rules.entries[rule_id]
        assert entry.patches_validated == frozenset({"16.19"})
        assert entry.evidence and "CC-324" in entry.evidence


def test_a_supported_rule_of_the_repository_can_only_be_active_in_the_current_version() -> None:
    """Guarda de identidade: o carregador recusa a ativação numa versão diferente da corrente."""
    rules = load_empirical_rules(CONFIG, reconstruction_version=VERSION)
    for rule_id, entry in rules.entries.items():
        if entry.status == "EMPIRICALLY_SUPPORTED":
            assert entry.activated_in_version == VERSION, rule_id


# ---------------------------------------------------------------------------
# ativação por patch e proveniência
# ---------------------------------------------------------------------------


def test_a_supported_rule_is_enabled_only_in_its_validated_patches() -> None:
    rules = parse_empirical_rules(_supported(_document()), reconstruction_version=VERSION)
    assert rules.enabled_for("16.17") == {"R5"}
    assert rules.enabled_for("16.16") == frozenset()  # fora dos patches validados: v6
    assert rules.enabled_for("DEMO-1") == frozenset()


def test_provenance_lists_only_enabled_rules_with_evidence() -> None:
    document = _supported(_supported(_document(), "R1", ("16.16", "16.17")), "R5")
    rules = parse_empirical_rules(document, reconstruction_version=VERSION)
    provenance = rules.provenance_for("16.17")
    assert [item["ruleId"] for item in provenance] == ["R1", "R5"]
    assert provenance[0]["patchesValidated"] == ["16.16", "16.17"]
    assert {"ruleVersion", "status", "contraRate", "counterExamples", "evidence"} <= set(
        provenance[0]
    )
    assert rules.provenance_for("16.16") == [provenance[0]]
    assert rules.provenance_for("15.1") == []


def test_disabled_registry_enables_nothing() -> None:
    disabled = EmpiricalRules.disabled()
    assert disabled.enabled_for("16.17") == frozenset() and disabled.provenance_for("16.17") == []


# ---------------------------------------------------------------------------
# rejeição do que não é válido (nunca liga uma regra por engano)
# ---------------------------------------------------------------------------


def _mutations() -> list[tuple[str, callable]]:
    def drop_rule(d: dict) -> None:
        del d["rules"]["R6"]

    def extra_rule(d: dict) -> None:
        d["rules"]["R9"] = copy.deepcopy(d["rules"]["R1"])

    def unknown_key(d: dict) -> None:
        d["rules"]["R1"]["extra"] = 1

    def bad_status(d: dict) -> None:
        d["rules"]["R1"]["status"] = "APPROVED"

    def pending_with_patches(d: dict) -> None:
        _pending(d, "R1")["patchesValidated"] = ["16.17"]

    def pending_with_evidence(d: dict) -> None:
        _pending(d, "R1")["evidence"] = "x"

    def supported_without_patches(d: dict) -> None:
        _supported(d, "R1", ())

    def supported_without_evidence(d: dict) -> None:
        _supported(d, "R1")["rules"]["R1"]["evidence"] = "  "

    def supported_bad_rate(d: dict) -> None:
        _supported(d, "R1")["rules"]["R1"]["contraRate"] = 1.5

    def supported_bool_rate(d: dict) -> None:
        _supported(d, "R1")["rules"]["R1"]["contraRate"] = True

    def supported_negative_examples(d: dict) -> None:
        _supported(d, "R1")["rules"]["R1"]["counterExamples"] = -1

    def supported_wrong_version(d: dict) -> None:
        _supported(d, "R1")["rules"]["R1"]["activatedInVersion"] = VERSION - 1

    def supported_bool_version(d: dict) -> None:
        _supported(d, "R1")["rules"]["R1"]["activatedInVersion"] = True

    def duplicate_patch(d: dict) -> None:
        _supported(d, "R1", ("16.17", "16.17"))

    def r4s_without_items(d: dict) -> None:
        _supported(d, "R4s")
        d["systemItemIds"] = []

    def duplicate_item(d: dict) -> None:
        d["systemItemIds"] = [1202, 1202]

    def bool_item(d: dict) -> None:
        d["systemItemIds"] = [True]

    def wrong_schema(d: dict) -> None:
        d["schemaVersion"] = "v2"

    def extra_top_key(d: dict) -> None:
        d["surprise"] = 1

    def bad_rule_version(d: dict) -> None:
        d["rules"]["R1"]["ruleVersion"] = 0

    def empty_description(d: dict) -> None:
        d["rules"]["R1"]["description"] = ""

    functions = [
        drop_rule,
        extra_rule,
        unknown_key,
        bad_status,
        pending_with_patches,
        pending_with_evidence,
        supported_without_patches,
        supported_without_evidence,
        supported_bad_rate,
        supported_bool_rate,
        supported_negative_examples,
        supported_wrong_version,
        supported_bool_version,
        duplicate_patch,
        r4s_without_items,
        duplicate_item,
        bool_item,
        wrong_schema,
        extra_top_key,
        bad_rule_version,
        empty_description,
    ]
    return [(fn.__name__, fn) for fn in functions]


@pytest.mark.parametrize(("name", "mutate"), _mutations(), ids=[n for n, _ in _mutations()])
def test_invalid_documents_are_rejected_with_a_safe_code(name: str, mutate: callable) -> None:
    document = _document()
    mutate(document)
    with pytest.raises(EmpiricalRulesConfigError) as caught:
        parse_empirical_rules(document, reconstruction_version=VERSION)
    assert caught.value.code == "EMPIRICAL_RULES_CONFIG_INVALID"


def test_non_mapping_document_is_rejected() -> None:
    with pytest.raises(EmpiricalRulesConfigError):
        parse_empirical_rules(["R1"], reconstruction_version=VERSION)


def test_unreadable_and_malformed_files_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(EmpiricalRulesConfigError):
        load_empirical_rules(tmp_path / "nao-existe.yaml", reconstruction_version=VERSION)
    broken = tmp_path / "quebrado.yaml"
    broken.write_text("rules: [unclosed", encoding="utf-8")
    with pytest.raises(EmpiricalRulesConfigError):
        load_empirical_rules(broken, reconstruction_version=VERSION)


def test_missing_file_means_all_rules_disabled_but_an_invalid_file_still_fails(
    tmp_path: Path,
) -> None:
    assert (
        load_empirical_rules_or_disabled(tmp_path / "ausente.yaml", reconstruction_version=VERSION)
        == EmpiricalRules.disabled()
    )
    invalid = tmp_path / "invalido.yaml"
    invalid.write_text("schemaVersion: v9\n", encoding="utf-8")
    with pytest.raises(EmpiricalRulesConfigError):
        load_empirical_rules_or_disabled(invalid, reconstruction_version=VERSION)


# ---------------------------------------------------------------------------
# R4w — pré-passo puro
# ---------------------------------------------------------------------------


def _event(
    index: int, ts: int, participant: int, item: int, type_: str = "ITEM_PURCHASED"
) -> TimelineEvent:
    return TimelineEvent("M", index, ts, type_, {"participantId": participant, "itemId": item})


def test_r4w_reassigns_the_participant_zero_world_atlas_purchases_to_the_supports() -> None:
    events = [
        _event(0, 0, 0, 3865),
        _event(1, 0, 0, 3865),
        _event(2, 5000, 3, 1038),
        _event(3, 9000, 8, 3865),
    ]
    result = attribute_system_purchases(events, [8, 3])
    assert [e.payload["participantId"] for e in result] == [
        3,
        8,
        3,
        8,
    ]  # ordem de fonte × Apoios crescentes
    assert events[0].payload["participantId"] == 0  # a lista original não é mutada
    assert [e.source_index for e in result] == [0, 1, 2, 3] and result[2] == events[2]


@pytest.mark.parametrize("supports", [[], [3], [3, 8, 9]], ids=["nenhum", "um", "tres"])
def test_r4w_fails_safe_when_the_counts_differ(supports: list[int]) -> None:
    events = [_event(0, 0, 0, 3865), _event(1, 0, 0, 3865)]
    assert attribute_system_purchases(events, supports) == events


def test_r4w_leaves_other_participant_zero_events_alone() -> None:
    events = [_event(0, 0, 0, 1038), _event(1, 0, 0, 3865, "ITEM_DESTROYED"), _event(2, 0, 0, 3865)]
    assert attribute_system_purchases(events, [3]) == [events[0], events[1], _event(2, 0, 3, 3865)]
    assert attribute_system_purchases([], [3, 8]) == []
