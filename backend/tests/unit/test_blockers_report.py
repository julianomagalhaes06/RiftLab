from collections import Counter
from uuid import uuid4

from riftlab.reports.blockers import (
    NO_PRE_DECISION_REASON,
    BlockerDecision,
    _aggregate,
    _in_scope,
    _pre_decision_reasons,
    render_blockers_report,
)


def test_first_reason_counts_decisions_and_cumulative_codes_once() -> None:
    catalog_id = uuid4()
    decisions = [
        BlockerDecision(
            "16.20",
            420,
            False,
            True,
            catalog_id,
            (("UNDO", 2003), ("UNDO", 2003), ("DESTROY", 1001)),
        ),
        BlockerDecision("16.20", 420, False, True, catalog_id, ()),
        BlockerDecision("16.20", 420, True, True, catalog_id, ()),
    ]

    result = _aggregate(
        decisions,
        item_names={(catalog_id, 2003): "Poção"},
        filter_in_scope=True,
        expected_contexts={("16.20", 420), ("16.20", 440)},
        extraction_counts=Counter({("16.20", 420): 2}),
        imported_counts=Counter({("16.20", 420): 2}),
        missing_version_counts=Counter(),
        missing_extraction_counts=Counter(),
    )

    assert result["totals"] == {
        "decisions": 3,
        "sourceDecisions": 3,
        "eligible": 1,
        "ineligible": 2,
        "inScope": 3,
        "outOfScope": 0,
        "eligiblePct": 33.33,
        "ineligiblePct": 66.67,
        "inScopePct": 100.0,
    }
    assert result["firstReasonByItem"] == [
        {
            "code": NO_PRE_DECISION_REASON,
            "itemId": None,
            "itemName": None,
            "decisions": 1,
            "denominator": 2,
            "pctOfIneligible": 50.0,
        },
        {
            "code": "UNDO",
            "itemId": 2003,
            "itemName": "Poção",
            "decisions": 1,
            "denominator": 2,
            "pctOfIneligible": 50.0,
        },
    ]
    assert {row["code"]: row["decisions"] for row in result["cumulativeByCode"]} == {
        "DESTROY": 1,
        "UNDO": 1,
    }
    assert result["byPatchQueue"]["16.20/440"] == {
        "patch": "16.20",
        "queueId": 440,
        "importedMatches": 0,
        "matchesWithoutCurrentVersions": 0,
        "matchesWithoutCurrentExtraction": 0,
        "decisions": 0,
        "eligible": 0,
        "ineligible": 0,
        "inScope": 0,
        "currentExtractionMatches": 0,
        "measurementStatus": "NOT_MEASURED",
        "eligiblePct": None,
        "ineligiblePct": None,
    }


def test_missing_quality_and_out_of_scope_reason_are_explicit() -> None:
    assert _pre_decision_reasons(None) == ()
    assert _pre_decision_reasons({"preDecisionQuality": {"reasons": "bad"}}) == ()
    assert not _in_scope([{"code": "OUT_OF_SCOPE", "subReasons": ["QUEUE_OUT_OF_SCOPE"]}])
    assert not _in_scope(
        [{"code": "OUT_OF_SCOPE", "subReasons": ["MAIN_ITEM_ORDINAL_BEYOND_SECOND"]}]
    )
    assert _in_scope([{"code": "OUT_OF_SCOPE", "subReasons": ["INVENTORY_UNCERTAIN"]}])
    assert not _in_scope(
        [
            {"code": "OUT_OF_SCOPE", "subReasons": ["MAIN_ITEM_ORDINAL_BEYOND_SECOND"]},
            {"code": "OUT_OF_SCOPE", "subReasons": ["CHAMPION_OUT_OF_SCOPE"]},
        ]
    )


def test_markdown_uses_aggregate_denominators_and_has_no_identity_fields() -> None:
    data = {
        "measured": False,
        "filters": {"patch": "16.20", "queueId": 440, "inScopeOnly": True},
        "totals": {
            "decisions": 0,
            "sourceDecisions": 0,
            "eligible": 0,
            "ineligible": 0,
            "inScope": 0,
            "outOfScope": 0,
            "eligiblePct": None,
            "ineligiblePct": None,
            "inScopePct": None,
        },
        "versions": [],
        "missingCurrentExtractionMatches": 0,
        "matchesWithoutCurrentVersions": 0,
        "currentVersionFailureCodes": {},
        "importedMatches": 0,
        "byPatchQueue": {
            "16.20/440": {
                "patch": "16.20",
                "queueId": 440,
                "measurementStatus": "NOT_MEASURED",
                "currentExtractionMatches": 0,
                "decisions": 0,
                "eligible": 0,
                "eligiblePct": None,
                "ineligible": 0,
                "ineligiblePct": None,
            }
        },
        "patchReadiness": {"16.20": None},
        "healthByContext": [
            {
                "patch": "16.20",
                "queueId": 440,
                "application": "INHERITED",
                "sourcePatch": "16.19",
                "rulesFingerprint": "a" * 64,
                "reconstructionVersion": 11,
                "cleanParticipants": 0,
                "failures": 0,
                "contributingMatches": 0,
                "wilsonUpper": 1.0,
                "verdict": "NO_SAMPLE",
                "isInvalidation": False,
                "computedAt": None,
            }
        ],
        "firstReasonByItem": [],
        "cumulativeByCode": [],
    }
    rendered = render_blockers_report(data)
    assert "denominador 0" in rendered
    assert "NOT_MEASURED" in rendered
    assert "matchId" not in rendered and "PUUID" not in rendered and "subjectHash" not in rendered
