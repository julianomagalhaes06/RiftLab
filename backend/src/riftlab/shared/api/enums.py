"""Enumeracoes fechadas explicitamente pelos documentos de contrato. So entram
aqui valores que os docs enumeram por completo (com "e"/"ou" exaustivo); campos
onde os docs dao so um exemplo ou uma lista "minima" (ex.: support.level,
reasonCodes) ficam como str validada em vez de enum, para nao inventar um
fechamento que o contrato nao afirma — ver shared/api/common.py e analyses.py.
"""

from __future__ import annotations

from enum import StrEnum


class ImportMode(StrEnum):
    """docs/contracts.md: POST /api/v1/imports {matchId, mode: RIOT ou DEMO}."""

    RIOT = "RIOT"
    DEMO = "DEMO"


class ImportJobState(StrEnum):
    """docs/architecture.md: QUEUED -> RUNNING -> SUCCEEDED; RUNNING ->
    RETRY_WAIT -> RUNNING; RUNNING -> FAILED."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ImportJobKind(StrEnum):
    """docs/contracts.md: DELETE /matches/{id} cria "job de expurgo,
    consultavel em /imports/{jobId} com kind=PURGE" — import comum e o padrao
    implicito nas demais rotas de /imports."""

    IMPORT = "IMPORT"
    PURGE = "PURGE"


class PreparationStatus(StrEnum):
    """Estado da preparação de uma partida importada (CC-205): catálogo ->
    reconstrução -> extração de decisões. PENDING = na fila ou aguardando
    nova tentativa (ver `retryAt`); RUNNING = em execução por um worker;
    READY = decisões extraídas para as versões correntes; FAILED = falhou
    (ver `errorCode`; pode ser retentada); NOT_REQUESTED = partida importada
    sem preparação registrada (POST /matches/{id}/preparation a inicia)."""

    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    READY = "READY"
    FAILED = "FAILED"


class Priority(StrEnum):
    """docs/contracts.md: formulario de objetivo {priority:
    BALANCED|SURVIVAL|DAMAGE|UTILITY}."""

    BALANCED = "BALANCED"
    SURVIVAL = "SURVIVAL"
    DAMAGE = "DAMAGE"
    UTILITY = "UTILITY"


class BudgetSource(StrEnum):
    """docs/domain-model.md: budget_source: UNKNOWN, FRAME_ESTIMATE,
    USER_PROVIDED."""

    UNKNOWN = "UNKNOWN"
    FRAME_ESTIMATE = "FRAME_ESTIMATE"
    USER_PROVIDED = "USER_PROVIDED"


class AnalysisStatus(StrEnum):
    """docs/contracts.md: status: AVAILABLE, INSUFFICIENT_EVIDENCE ou
    UNSUPPORTED."""

    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNSUPPORTED = "UNSUPPORTED"


class AnalysisEngine(StrEnum):
    """docs/contracts.md: engine: POPULARITY, RULES, CONTEXTUAL_RANKER."""

    POPULARITY = "POPULARITY"
    RULES = "RULES"
    CONTEXTUAL_RANKER = "CONTEXTUAL_RANKER"


class ReportFormat(StrEnum):
    """docs/contracts.md: GET .../report | format=json ou html."""

    JSON = "json"
    HTML = "html"


class LearningAttemptState(StrEnum):
    """docs/domain-model.md: OPEN -> ANSWERED -> REVEALED, ou OPEN -> REVEALED
    por pedido explicito; REVEALED e monotonico (nunca volta a ficar cega)."""

    OPEN = "OPEN"
    ANSWERED = "ANSWERED"
    REVEALED = "REVEALED"
