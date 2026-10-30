"""POST /api/v1/objectives/interpret (CC-504).

Converte até 500 caracteres num objetivo do formulário ou em NEEDS_INPUT. Não persiste nem
registra o texto; o objetivo só vale depois de confirmado pela pessoa na interface.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from riftlab.errors import ProblemDetail
from riftlab.objectives.application import build_parser, interpret_objective
from riftlab.shared.api.objectives import ObjectiveInterpretRequest, ObjectiveInterpretResult

router = APIRouter(prefix="/api/v1/objectives", tags=["objectives"])


@router.post(
    "/interpret", response_model=ObjectiveInterpretResult, responses={422: {"model": ProblemDetail}}
)
async def post_interpret(
    body: ObjectiveInterpretRequest, request: Request
) -> ObjectiveInterpretResult:
    settings = request.app.state.settings
    parser = getattr(request.app.state, "objective_parser", None) or build_parser(
        settings.objective_parser,
        settings.objective_model_url,
        settings.objective_model_version,
        settings.objective_model_socket_path,
    )
    result = await interpret_objective(parser, body.text, settings.objective_parser_timeout_seconds)
    return ObjectiveInterpretResult.model_validate(result.as_payload())
