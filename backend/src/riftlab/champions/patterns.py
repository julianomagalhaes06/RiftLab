from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import Field

from riftlab.shared.api.common import CamelModel
from riftlab.shared.api.matches import ChampionAsset

MIN_PATTERN_SAMPLE = 5
SAMPLE_LIMIT = 200


class ChampionChoice(CamelModel):
    champion_id: int
    champion: ChampionAsset | None
    matches: int


class ObservedItem(CamelModel):
    item_id: int
    name: str | None
    data_dragon_version: str


class ObservedPattern(CamelModel):
    items: list[ObservedItem]
    count: int
    denominator: int


class ChampionSituation(CamelModel):
    match_id: str
    participant_id: int
    decision_id: UUID
    timestamp_ms: int


class ChampionPatterns(CamelModel):
    """`patches`: opções do filtro, do mais novo ao mais antigo. `current_patch`: patch atual do
    jogo (padrão do filtro). `analysis_patches`: janela de análise; fora dela, `OUT_OF_SCOPE`."""

    patches: list[str]
    current_patch: str | None = None
    analysis_patches: list[str] = Field(default_factory=list)
    patch: str | None
    role: Literal["BOTTOM"] = "BOTTOM"
    phase: Literal["first", "second", "sequence"]
    champions: list[ChampionChoice]
    champion_id: int | None
    source: Literal["IMPORTED_REAL_MATCHES"] = "IMPORTED_REAL_MATCHES"
    sample_limit: int = SAMPLE_LIMIT
    total_participations: int = 0
    sampled_participations: int = 0
    observed_decisions: int = 0
    eligible_decisions: int = 0
    pattern_denominator: int = 0
    minimum_sample: int = MIN_PATTERN_SAMPLE
    status: Literal["AVAILABLE", "SMALL_SAMPLE", "NO_ELIGIBLE", "OUT_OF_SCOPE", "EMPTY"]
    patterns: list[ObservedPattern] = Field(default_factory=list)
    situations: list[ChampionSituation] = Field(default_factory=list)


@dataclass(frozen=True)
class Observation:
    match_id: str
    participant_id: int
    ordinal: int
    item_id: int
    item_name: str | None
    catalog_version: str
    eligible: bool
    decision_id: UUID
    timestamp_ms: int


def summarize_observations(
    observations: list[Observation], phase: Literal["first", "second", "sequence"]
) -> tuple[list[ObservedPattern], int, int, int, list[ChampionSituation]]:
    ordinals = {1} if phase == "first" else {2} if phase == "second" else {1, 2}
    selected = [row for row in observations if row.ordinal in ordinals]
    eligible = [row for row in selected if row.eligible]
    grouped: dict[tuple[str, int], list[Observation]] = defaultdict(list)
    for row in selected:
        grouped[row.match_id, row.participant_id].append(row)
    counts: Counter[tuple[tuple[int, str | None, str], ...]] = Counter()
    for rows in grouped.values():
        if len(rows) != len(ordinals) or {row.ordinal for row in rows} != ordinals:
            continue
        ordered = sorted(rows, key=lambda row: row.ordinal)
        if not all(row.eligible for row in ordered):
            continue
        if any(
            left.timestamp_ms >= right.timestamp_ms
            for left, right in zip(ordered, ordered[1:], strict=False)
        ):
            continue
        counts[tuple((row.item_id, row.item_name, row.catalog_version) for row in ordered)] += 1
    denominator = sum(counts.values())
    patterns = (
        [
            ObservedPattern(
                items=[
                    ObservedItem(item_id=item, name=name, data_dragon_version=version)
                    for item, name, version in trajectory
                ],
                count=count,
                denominator=denominator,
            )
            for trajectory, count in sorted(
                counts.items(), key=lambda entry: (-entry[1], str(entry[0]))
            )[:6]
        ]
        if denominator >= MIN_PATTERN_SAMPLE
        else []
    )
    situations = [
        ChampionSituation(
            match_id=row.match_id,
            participant_id=row.participant_id,
            decision_id=row.decision_id,
            timestamp_ms=row.timestamp_ms,
        )
        for row in sorted(eligible, key=lambda row: (row.match_id, row.timestamp_ms))[:6]
    ]
    return patterns, len(selected), len(eligible), denominator, situations
