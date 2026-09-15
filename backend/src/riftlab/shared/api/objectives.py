"""POST /api/v1/objectives/interpret (docs/contracts.md, RF09/UC03).
Schema-only: nenhuma rota montada nesta etapa (CC-503/504)."""

from __future__ import annotations

from pydantic import Field

from riftlab.shared.api.common import CamelModel
from riftlab.shared.api.enums import Priority

OBJECTIVE_TEXT_MAX_LENGTH = 500


class ObjectiveForm(CamelModel):
    """Formulario estruturado citado em docs/contracts.md e reaproveitado como
    o campo "objective" de POST /analyses e /learning-attempts."""

    priority: Priority


class ObjectiveInterpretRequest(CamelModel):
    """{text} ate 500 caracteres. Texto e dado, nunca instrucao de ferramenta
    (docs/contracts.md) — este schema so garante tamanho/tipo; o interpretador
    (CC-503/504) nao tem acesso a shell, rede ou segredos."""

    text: str = Field(min_length=1, max_length=OBJECTIVE_TEXT_MAX_LENGTH)


class ObjectiveInterpretResult(CamelModel):
    """{status,objective,parserVersion}; NEEDS_INPUT quando ambiguo
    (docs/contracts.md). O contrato nao enumera o valor de status para o caso
    de sucesso (so cita NEEDS_INPUT para ambiguidade) — mantido como str
    validada, nao StrEnum fechado, para nao inventar o rotulo do caminho feliz
    antes do CC-503 decidir."""

    status: str = Field(min_length=1, max_length=40)
    objective: ObjectiveForm | None = None
    parser_version: str = Field(min_length=1, max_length=40)
