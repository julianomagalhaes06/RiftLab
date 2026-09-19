"""POST/GET/retry de /api/v1/imports (CC-201, docs/contracts.md). mode=RIOT
exige RIFTLAB_RIOT_API_KEY configurada no servidor — recusado aqui, antes de
qualquer job, quando ausente (nunca cria um job que o worker só descobriria
mais tarde que não pode processar)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request

from riftlab.errors import ProblemDetail
from riftlab.ingestion.application import get_import_job, request_import, retry_import
from riftlab.ingestion.domain import ImportJob
from riftlab.ingestion.domain import ImportMode as DomainImportMode
from riftlab.shared.api.enums import ImportJobKind, ImportJobState
from riftlab.shared.api.imports import ImportJobAccepted, ImportJobStatus, ImportRequest
from riftlab.shared.errors import RiotKeyMissingError

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])

_NOT_FOUND_RESPONSE = {"model": ProblemDetail, "description": "Job de importação não encontrado."}
_INVALID_JOB_ID_RESPONSE = {
    "model": ProblemDetail,
    "description": "job_id não é um UUID válido.",
}


@router.post(
    "",
    status_code=202,
    response_model=ImportJobAccepted,
    responses={
        422: {
            "model": ProblemDetail,
            "description": (
                "VALIDATION_ERROR (payload inválido) ou RIOT_KEY_MISSING (modo RIOT pedido a "
                "um servidor sem chave da Riot configurada; nenhum job é criado)."
            ),
        }
    },
)
async def create_import(payload: ImportRequest, request: Request) -> ImportJobAccepted:
    mode = DomainImportMode(payload.mode.value)
    if mode == DomainImportMode.RIOT and request.app.state.settings.demo_mode:
        # 422 com código próprio RIOT_KEY_MISSING (errors.py), não 409/500: a
        # requisição em si é inválida para este servidor (nenhuma chave
        # configurada), não um conflito de estado nem uma falha interna.
        raise RiotKeyMissingError()
    job = await request_import(request.app.state.session_factory, payload.match_id, mode)
    return _to_accepted(job)


@router.get(
    "/{job_id}",
    response_model=ImportJobStatus,
    responses={404: _NOT_FOUND_RESPONSE, 422: _INVALID_JOB_ID_RESPONSE},
)
async def get_import(job_id: uuid.UUID, request: Request) -> ImportJobStatus:
    job = await get_import_job(request.app.state.session_factory, job_id)
    return _to_status(job)


@router.post(
    "/{job_id}/retry",
    status_code=202,
    response_model=ImportJobAccepted,
    responses={
        404: _NOT_FOUND_RESPONSE,
        409: {"model": ProblemDetail, "description": "Job ainda ativo ou não está FAILED."},
        422: _INVALID_JOB_ID_RESPONSE,
    },
)
async def retry_import_route(job_id: uuid.UUID, request: Request) -> ImportJobAccepted:
    job = await retry_import(request.app.state.session_factory, job_id)
    return _to_accepted(job)


def _status_url(job_id: uuid.UUID) -> str:
    return f"/api/v1/imports/{job_id}"


def _to_accepted(job: ImportJob) -> ImportJobAccepted:
    return ImportJobAccepted(
        job_id=job.id,
        status=ImportJobState(job.state.value),
        status_url=_status_url(job.id),
    )


def _to_status(job: ImportJob) -> ImportJobStatus:
    # error_code só é != None em FAILED/RETRY_WAIT por construção do
    # repositório (reserve_next_job/mark_import_succeeded sempre limpam),
    # não por checagem defensiva aqui.
    return ImportJobStatus(
        status=ImportJobState(job.state.value),
        kind=ImportJobKind.IMPORT,
        attempts=job.attempts,
        error_code=job.error_code,
        retry_at=job.retry_at,
        match_id=job.match_id,
    )
