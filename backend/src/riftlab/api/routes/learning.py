"""POST /api/v1/learning-attempts e sub-rotas (CC-304, docs/learning-design.md).

Escolha antes da revelação: antes de REVEALED o estado não tem posição, pontuação,
custo nem compra histórica; pistas mostram só fatos disponíveis; revelar é
explícito, idempotente e cria (uma vez) a análise da tentativa.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response

from riftlab.api.routes.analyses import _configs
from riftlab.errors import ProblemDetail
from riftlab.learning.application import (
    answer_attempt,
    create_attempt,
    get_attempt,
    hint_attempt,
    reveal_attempt,
)
from riftlab.shared.api.learning import (
    LearningAttemptAnswerRequest,
    LearningAttemptCreateRequest,
    LearningAttemptHintRequest,
    LearningAttemptPublicState,
    LearningAttemptRevealRequest,
)

router = APIRouter(prefix="/api/v1/learning-attempts", tags=["learning"])
_ERRORS: dict[int | str, dict[str, object]] = {
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
}


@router.post("", status_code=201, response_model=LearningAttemptPublicState, responses=_ERRORS)
async def post_attempt(
    body: LearningAttemptCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)],
) -> LearningAttemptPublicState:
    payload = body.model_dump(mode="json", by_alias=True)
    state, created = await create_attempt(
        request.app.state.session_factory, payload, idempotency_key, _configs(request)
    )
    if not created:
        response.status_code = 200
    return LearningAttemptPublicState.model_validate(state)


@router.get("/{attempt_id}", response_model=LearningAttemptPublicState, responses=_ERRORS)
async def read_attempt(attempt_id: uuid.UUID, request: Request) -> LearningAttemptPublicState:
    state = await get_attempt(request.app.state.session_factory, attempt_id, _configs(request))
    return LearningAttemptPublicState.model_validate(state)


@router.post("/{attempt_id}/answer", response_model=LearningAttemptPublicState, responses=_ERRORS)
async def post_answer(
    attempt_id: uuid.UUID, body: LearningAttemptAnswerRequest, request: Request
) -> LearningAttemptPublicState:
    state = await answer_attempt(
        request.app.state.session_factory,
        attempt_id,
        body.model_dump(mode="json", by_alias=True),
        _configs(request),
    )
    return LearningAttemptPublicState.model_validate(state)


@router.post("/{attempt_id}/hint", response_model=LearningAttemptPublicState, responses=_ERRORS)
async def post_hint(
    attempt_id: uuid.UUID, body: LearningAttemptHintRequest, request: Request
) -> LearningAttemptPublicState:
    state = await hint_attempt(
        request.app.state.session_factory, attempt_id, body.level, _configs(request)
    )
    return LearningAttemptPublicState.model_validate(state)


@router.post("/{attempt_id}/reveal", response_model=LearningAttemptPublicState, responses=_ERRORS)
async def post_reveal(
    attempt_id: uuid.UUID, body: LearningAttemptRevealRequest, request: Request
) -> LearningAttemptPublicState:
    state = await reveal_attempt(request.app.state.session_factory, attempt_id, _configs(request))
    return LearningAttemptPublicState.model_validate(state)
