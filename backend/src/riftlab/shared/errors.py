class DomainError(Exception):
    """Erro de domínio; nunca carrega SQL, DSN ou stack trace de infraestrutura."""


class ConflictError(DomainError):
    """Violação de identidade ativa ou unicidade (ex.: job ativo duplicado)."""


class NotFoundError(DomainError):
    """Agregado solicitado não existe."""
