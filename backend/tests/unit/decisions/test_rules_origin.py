from riftlab.decisions.queries import rules_origin


def test_rules_origin_does_not_present_all_withheld_rules_as_validated():
    provenance = [{"ruleId": "R8", "application": "WITHHELD", "appliedFromPatch": "16.19"}]

    assert rules_origin(provenance) == ("NONE", None)


def test_rules_origin_uses_applied_rule_when_provenance_also_contains_withheld():
    provenance = [
        {"ruleId": "R8", "application": "WITHHELD", "appliedFromPatch": "16.19"},
        {"ruleId": "R5", "application": "INHERITED", "appliedFromPatch": "16.19"},
    ]

    assert rules_origin(provenance) == ("INHERITED", "16.19")
