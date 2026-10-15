from dataclasses import replace
from uuid import uuid4

from riftlab.champions.patterns import Observation, summarize_observations


def observation(match: str, ordinal: int, *, eligible: bool = True) -> Observation:
    return Observation(
        match,
        1,
        ordinal,
        3000 + ordinal,
        f"Item {ordinal}",
        "16.19.1",
        eligible,
        uuid4(),
        ordinal * 60_000,
    )


def test_sequence_excludes_gaps_uncertainty_duplicates_and_inverted_time():
    rows = [observation(f"match-{index}", ordinal) for index in range(5) for ordinal in (1, 2)]
    rows += [observation("gap", 2)]
    rows += [observation("uncertain", 1), observation("uncertain", 2, eligible=False)]
    rows += [observation("duplicate", 1), observation("duplicate", 1), observation("duplicate", 2)]
    rows += [observation("inverted", 1), replace(observation("inverted", 2), timestamp_ms=1)]
    patterns, observed, eligible, denominator, _ = summarize_observations(rows, "sequence")
    assert (observed, eligible, denominator) == (18, 17, 5)
    assert len(patterns) == 1
    assert patterns[0].count == patterns[0].denominator == 5
    assert [item.item_id for item in patterns[0].items] == [3001, 3002]


def test_small_samples_abstain_but_keep_access_to_eligible_context():
    patterns, observed, eligible, denominator, situations = summarize_observations(
        [observation("one", 1), observation("two", 1, eligible=False)], "first"
    )
    assert patterns == []
    assert (observed, eligible, denominator) == (2, 1, 1)
    assert len(situations) == 1
    assert situations[0].match_id == "one"


def test_phase_and_participant_isolation_and_catalog_identity():
    first = observation("shared", 1)
    other_participant = replace(observation("shared", 2), participant_id=2)
    patterns, observed, eligible, denominator, _ = summarize_observations(
        [first, other_participant], "sequence"
    )
    assert (patterns, observed, eligible, denominator) == ([], 2, 2, 0)
    rows = [observation(f"match-{index}", 1) for index in range(5)]
    rows.append(replace(observation("other-catalog", 1), catalog_version="16.18.1"))
    patterns, observed, eligible, denominator, _ = summarize_observations(rows, "first")
    assert (observed, eligible, denominator) == (6, 6, 6)
    assert [pattern.count for pattern in patterns] == [5, 1]
    assert {pattern.items[0].data_dragon_version for pattern in patterns} == {"16.19.1", "16.18.1"}


def test_empty_and_second_phase_have_exact_denominators():
    assert summarize_observations([], "second") == ([], 0, 0, 0, [])
    _, observed, eligible, denominator, _ = summarize_observations(
        [observation("one", 1), observation("one", 2)], "second"
    )
    assert (observed, eligible, denominator) == (1, 1, 1)
