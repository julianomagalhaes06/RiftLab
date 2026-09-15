from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

PAGINATION_DEFAULT_LIMIT = 20
PAGINATION_MAX_LIMIT = 100


class CamelModel(BaseModel):
    """Base dos schemas de contrato (docs/contracts.md): JSON em camelCase,
    aceita o nome Python ou o alias na entrada, campo desconhecido rejeitado
    (contrato fechado — nunca aceitar payload extra silenciosamente)."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


# Match ID real (docs/contracts.md): "^BR1_[0-9]{1,20}$"; nao aceitar host, URL
# ou caminho local de usuario. Match ID demo: manifesto fechado do CC-104
# (backend/src/riftlab/ingestion/infrastructure/fixtures/manifest.json), formato
# real observado "DEMO-0001".."DEMO-0007" — o schema so verifica o formato
# (DEMO-dddd); pertencer ao manifesto de fato e responsabilidade do adapter
# (SourceNotFoundError), nao deste contrato de borda HTTP.
MATCH_ID_REAL_PATTERN = re.compile(r"^BR1_[0-9]{1,20}$")
MATCH_ID_DEMO_PATTERN = re.compile(r"^DEMO-\d{4}$")


def _check_any_match_id(value: str) -> str:
    if not (MATCH_ID_REAL_PATTERN.fullmatch(value) or MATCH_ID_DEMO_PATTERN.fullmatch(value)):
        raise ValueError(
            "matchId nao corresponde ao formato BR1 real nem ao manifesto demo "
            "fechado (docs/contracts.md)"
        )
    return value


# Uso geral (rotas de leitura: GET /matches/{id}, decisions, etc.) onde o modo
# de origem da partida ja nao esta mais em questao — qualquer partida
# persistida, real ou demo, tem um matchId valido em um dos dois formatos.
# POST /imports discrimina por mode explicitamente (ver imports.py); aqui so
# se rejeita o que nunca poderia ser um matchId legitimo.
MatchId = Annotated[str, AfterValidator(_check_any_match_id)]


class CursorPage[ItemT](CamelModel):
    """Envelope de lista paginada (docs/contracts.md): items + nextCursor,
    ordenacao estavel por timestamp/id definida por quem monta a query —
    ausencia de proxima pagina e nextCursor=None, nunca string vazia."""

    items: list[ItemT]
    next_cursor: str | None = None


class PaginationParams(CamelModel):
    cursor: str | None = None
    limit: int = Field(default=PAGINATION_DEFAULT_LIMIT, ge=1, le=PAGINATION_MAX_LIMIT)


# Conjunto minimo de reason codes citado em docs/contracts.md, para
# referencia/teste. O contrato usa a palavra "minimos": reasonCodes de uma
# analise e list[str] aberta (engines futuros podem adicionar codigos), nao um
# enum fechado — por isso isto e uma constante de referencia, nao um StrEnum.
MINIMUM_ANALYSIS_REASON_CODES: frozenset[str] = frozenset(
    {
        "MISSING_CATALOG",
        "OUT_OF_SCOPE",
        "INVENTORY_UNCERTAIN",
        "STALE_FRAME",
        "UNKNOWN_BUDGET",
        "SMALL_CONTEXT_SAMPLE",
        "OOD_CONTEXT",
        "MODEL_UNAVAILABLE",
    }
)


def example_dump(model: BaseModel) -> dict[str, Any]:
    """Serializa um exemplo pelo alias camelCase, igual ao corpo HTTP real."""
    return model.model_dump(mode="json", by_alias=True)
