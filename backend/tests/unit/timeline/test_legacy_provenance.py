from copy import deepcopy

from riftlab.timeline.application import _rule_set
from riftlab.timeline.legacy_provenance import (
    LEGACY_SOURCE_COMMIT,
    LEGACY_SOURCE_SHA256,
    legacy_v10_context,
    normalize_known_v10_provenance,
)


def test_manifest_hashes_and_known_v10_context_normalize_missing_r4s_parameter():
    context = legacy_v10_context()
    assert context["sourceCommit"] == LEGACY_SOURCE_COMMIT
    assert context["sourceSha256"] == LEGACY_SOURCE_SHA256
    assert context["reconstructionVersion"] == 10
    assert len(context["systemItemIds"]) == 14

    normalized = normalize_known_v10_provenance(
        deepcopy(context["legacyProvenance"]),
        patch="16.19",
        reconstruction_version=10,
        has_catalog=True,
    )

    assert normalized is not None
    r4s = next(rule for rule in normalized if rule["ruleId"] == "R4s")
    assert r4s["systemItemIds"] == context["systemItemIds"]


def test_legacy_bridge_rejects_unknown_or_divergent_contexts():
    context = legacy_v10_context()
    cases = []
    changed_evidence = deepcopy(context["legacyProvenance"])
    changed_evidence[0]["evidence"] = "contexto não reconhecido"
    cases.append((changed_evidence, "16.19", 10, True))
    changed_origin = deepcopy(context["legacyProvenance"])
    changed_origin[0]["appliedFromPatch"] = "16.18"
    cases.append((changed_origin, "16.19", 10, True))
    changed_application = deepcopy(context["legacyProvenance"])
    changed_application[0]["application"] = "INHERITED"
    cases.append((changed_application, "16.19", 10, True))
    changed_parameter = deepcopy(context["legacyProvenance"])
    next(rule for rule in changed_parameter if rule["ruleId"] == "R4s")["systemItemIds"] = [1]
    cases.append((changed_parameter, "16.19", 10, True))
    cases.extend(
        [
            (deepcopy(context["legacyProvenance"]), "16.20", 10, True),
            (deepcopy(context["legacyProvenance"]), "16.19", 9, True),
            (deepcopy(context["legacyProvenance"]), "16.19", 10, False),
        ]
    )

    for provenance, patch, version, has_catalog in cases:
        assert (
            normalize_known_v10_provenance(
                provenance,
                patch=patch,
                reconstruction_version=version,
                has_catalog=has_catalog,
            )
            is None
        )


def test_known_legacy_parameter_must_still_match_current_runtime_parameter():
    context = legacy_v10_context()
    normalized = normalize_known_v10_provenance(
        deepcopy(context["legacyProvenance"]),
        patch="16.19",
        reconstruction_version=10,
        has_catalog=True,
    )
    assert normalized is not None
    divergent_current = deepcopy(normalized)
    next(rule for rule in divergent_current if rule["ruleId"] == "R4s")["systemItemIds"] = [1]
    assert _rule_set(normalized) != _rule_set(divergent_current)
