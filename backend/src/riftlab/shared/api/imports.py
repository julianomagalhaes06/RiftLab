"""POST /api/v1/imports, GET /api/v1/imports/{id}, POST .../retry
(docs/contracts.md). Schema-only: nenhuma rota montada nesta etapa (CC-201)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from riftlab.shared.api.common import (
    MATCH_ID_DEMO_PATTERN,
    MATCH_ID_REAL_PATTERN,
    CamelModel,
    MatchId,
)
from riftlab.shared.api.enums import ImportJobKind, ImportJobState, ImportMode


class ImportRequest(CamelModel):
    """matchId discriminado por mode (plano do CC-106): DEMO so aceita o
    formato do manifesto fechado (DEMO-dddd), RIOT so aceita o padrao BR1
    real. Nunca libera URL, caminho ou host — o padrao de cada modo ja exclui
    qualquer caractere fora de [A-Z0-9_-]."""

    match_id: str = Field(min_length=1, max_length=64)
    mode: ImportMode

    @model_validator(mode="after")
    def _match_id_matches_mode(self) -> ImportRequest:
        pattern = MATCH_ID_DEMO_PATTERN if self.mode == ImportMode.DEMO else MATCH_ID_REAL_PATTERN
        if not pattern.fullmatch(self.match_id):
            raise ValueError(
                f"matchId nao corresponde ao padrao esperado para mode={self.mode.value} "
                "(docs/contracts.md)"
            )
        return self


class ImportJobAccepted(CamelModel):
    """202 de POST /imports: job novo ou job ativo reaproveitado."""

    job_id: UUID
    status: ImportJobState
    status_url: str = Field(min_length=1, max_length=200)


class ImportJobStatus(CamelModel):
    """200 de GET /imports/{id}."""

    status: ImportJobState
    kind: ImportJobKind = ImportJobKind.IMPORT
    attempts: int = Field(ge=0)
    error_code: str | None = Field(default=None, min_length=1, max_length=60)
    retry_at: datetime | None = None
    match_id: MatchId | None = None

    @model_validator(mode="after")
    def _error_code_only_when_failed_or_waiting(self) -> ImportJobStatus:
        if self.error_code is not None and self.status not in (
            ImportJobState.FAILED,
            ImportJobState.RETRY_WAIT,
        ):
            raise ValueError(
                "errorCode so e valido em jobs FAILED ou RETRY_WAIT (docs/architecture.md: "
                "falhas transitorias entram em RETRY_WAIT com causa registrada)"
            )
        return self
