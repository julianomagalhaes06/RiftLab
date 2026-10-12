"""POST /api/v1/objectives/interpret (docs/contracts.md, RF09/UC03; rota no CC-504)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from riftlab.shared.api.common import CamelModel
from riftlab.shared.api.enums import Priority

OBJECTIVE_TEXT_MAX_LENGTH = 500


class ObjectiveForm(CamelModel):
    """Formulario estruturado citado em docs/contracts.md e reaproveitado como
    o campo "objective" de POST /analyses e /learning-attempts."""

    priority: Priority
    # CC-504: versão do interpretador quando o objetivo veio de texto confirmado; ausente
    # quando escolhido no formulário. Só rastreia: a reordenação usa apenas priority.
    parser_version: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,60}$")


class ObjectiveInterpretRequest(CamelModel):
    """{text} ate 500 caracteres. Texto e dado, nunca instrucao de ferramenta
    (docs/contracts.md) — este schema so garante tamanho/tipo; o interpretador
    (CC-503/504) nao tem acesso a shell, rede ou segredos."""

    text: str = Field(min_length=1, max_length=OBJECTIVE_TEXT_MAX_LENGTH)


class ObjectiveInterpretResult(CamelModel):
    """{status,objective,parserVersion} (docs/contracts.md) + provider e reasonCodes
    (CC-504: provedor rastreável). status INTERPRETED ou NEEDS_INPUT; NEEDS_INPUT traz
    objective nulo e abre o formulário (ambíguo, fora do domínio, instrução ao
    interpretador, saída inválida do modelo, TIMEOUT ou PROVIDER_ERROR)."""

    status: Literal["INTERPRETED", "NEEDS_INPUT"]
    objective: ObjectiveForm | None = None
    parser_version: str = Field(min_length=1, max_length=60)
    provider: Literal["RULES", "LOCAL_MODEL"]
    reason_codes: list[str] = Field(default_factory=list, max_length=10)
