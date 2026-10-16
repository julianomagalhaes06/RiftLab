from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from riftlab.ranked.application import ranked_me
from riftlab.ranked.domain import SOLO_QUEUE, RankedQueue

router = APIRouter(tags=["ranked"])


class Position(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    queue: RankedQueue
    season: str
    observed_at: datetime
    origin: str
    state: Literal["READY", "UNRANKED"]
    tier: str | None
    division: str | None
    league_points: int | None
    wins: int | None
    losses: int | None


class Change(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    since: datetime
    until: datetime
    kind: Literal["NOT_COMPARABLE", "CORRECTION", "TRANSITION", "SAME_DIVISION"]
    league_points_delta: int | None
    own_match_association: Literal["NONE", "ONE", "MULTIPLE"]


class RankedMe(BaseModel):
    status: Literal["READY", "UNRANKED", "UNAVAILABLE", "STALE"]
    reason: str | None
    position: Position | None
    change: Change | None


@router.get(
    "/api/v1/ranked/me",
    response_model=RankedMe,
    summary="Posição Solo/Duo ou Flex da conta local",
    description="Somente banco e conta local configurada; nenhuma chamada à Riot.",
)
async def get_ranked_me(request: Request, queue: RankedQueue = SOLO_QUEUE) -> RankedMe:
    data = await ranked_me(
        request.app.state.session_factory, request.app.state.settings, queue=queue
    )
    return RankedMe.model_validate(data)
