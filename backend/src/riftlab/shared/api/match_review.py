"""Contrato da revisão da partida (F1, CC-1003/CC-1004). Ids de participante 1..10 e de
item; nenhum identificador pessoal. Popularidade não é qualidade e a chance de vitória é
correlacional: os rótulos dizem o que é comum e quando a chance estimada mudou."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from riftlab.shared.api.common import CamelModel

PurchaseLabel = Literal["COMMON", "ALTERNATIVE", "UNCOMMON", "NO_REFERENCE"]
SkillLabel = Literal["MOST_COMMON", "ALTERNATIVE", "UNCOMMON", "NO_REFERENCE"]


class ReviewReferenceItem(CamelModel):
    item_id: int
    share: float = Field(ge=0, le=1)


class ReviewPurchase(CamelModel):
    decision_id: str
    timestamp_ms: int
    ordinal: int = Field(ge=1, le=5)
    item_id: int
    status: Literal["READY", "NOT_READY"]
    label: PurchaseLabel | None = None
    share: float | None = None
    rank: int | None = None
    support: int | None = None
    reference_top: list[ReviewReferenceItem] = Field(default_factory=list)


class ReviewSkillShare(CamelModel):
    priority: str
    share: float = Field(ge=0, le=1)


class ReviewSkills(CamelModel):
    priority: str | None
    label: SkillLabel
    share: float | None = None
    support: int
    most_common: list[ReviewSkillShare] = Field(default_factory=list)


class ReviewLaneStats(CamelModel):
    gold: int
    xp: int
    cs: int
    level: int


class ReviewLaneMinute(CamelModel):
    minute: int
    own: ReviewLaneStats
    opponent: ReviewLaneStats


class ReviewLane(CamelModel):
    opponent_id: int = Field(ge=1, le=10)
    minutes: list[ReviewLaneMinute]


class ReviewMomentEvent(CamelModel):
    kind: str
    team: int | None = None
    involves_participant: bool


class ReviewKeyMoment(CamelModel):
    minute: int
    delta: float
    probability_after: float = Field(ge=0, le=1)
    events: list[ReviewMomentEvent]


class ReviewWinPoint(CamelModel):
    timestamp_ms: int
    probability: float = Field(ge=0, le=1)


class ReviewWinProbability(CamelModel):
    model_version: str
    team: int
    points: list[ReviewWinPoint]
    key_moments: list[ReviewKeyMoment]


class ReviewCounts(CamelModel):
    purchases: int
    ready: int
    common: int
    alternative: int
    uncommon: int
    no_reference: int


class MatchReview(CamelModel):
    match_id: str
    participant_id: int = Field(ge=1, le=10)
    champion_id: int
    role: str | None
    patch: str
    queue: int
    reference_version: str | None
    reference_inherited: bool
    purchases: list[ReviewPurchase]
    skills: ReviewSkills | None
    lane: ReviewLane | None
    win_probability: ReviewWinProbability | None
    win_probability_unavailable: (
        Literal["MODEL_UNAVAILABLE", "MODEL_GATE_FAILED", "FRAMES_WITHOUT_XP"] | None
    ) = None
    counts: ReviewCounts
