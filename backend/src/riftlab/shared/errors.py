class DomainError(Exception):
    """Erro de domínio; nunca carrega SQL, DSN ou stack trace de infraestrutura."""


class ConflictError(DomainError):
    """Violação de identidade ativa ou unicidade (ex.: job ativo duplicado)."""


class NotFoundError(DomainError):
    """Agregado solicitado não existe."""


class SourceError(DomainError):
    """Erro ao carregar dados de uma fonte plugável (MatchSource/CatalogSource,
    CC-104). code é estável e seguro para diagnóstico; detail nunca ecoa corpo
    bruto, valor ofensivo ou segredo."""

    def __init__(self, *, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


class SourceNotFoundError(SourceError):
    """Identificador não reconhecido pela fonte (ex.: match_id/game_patch fora do
    manifesto fechado). A fonte demo nunca cai para outra fonte como fallback."""


class SourceSchemaError(SourceError):
    """JSON inválido, schema desconhecido ou campo estrutural obrigatório ausente
    (ex.: patch obrigatório ausente não recebe catálogo atual como substituto)."""


class SourceAuthError(SourceError):
    """Credencial rejeitada pela fonte remota (401/403). Falha permanente: o
    chamador nunca tenta de novo automaticamente, mesmo dentro do orçamento de
    tentativas (CC-105)."""


class SourceRateLimitedError(SourceError):
    """Limite de taxa da fonte remota excedido (HTTP 429) além do orçamento local
    de tentativas/tempo, ou orçamento local esgotado antes disso (CC-105). Nunca
    presume uma quota fixa: só reflete o que o servidor ou o próprio orçamento
    local realmente impôs."""

    def __init__(self, *, code: str, detail: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(code=code, detail=detail)
        self.retry_after_seconds = retry_after_seconds
