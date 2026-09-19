"""Validação de strings numéricas vindas de JSON externo (chaves de item, de
`maps`, de `participantFrames`, elementos de `from`/`into`)."""

from __future__ import annotations

from typing import Any


def is_ascii_digits(value: Any) -> bool:
    """String numérica só com dígitos ASCII 0-9. `str.isdigit()` sozinho aceita
    dígitos Unicode ("²", "٣") que `int()` rejeita ("²": ValueError cru) ou
    converte em silêncio ("٣" -> 3); uma chave assim nunca pode virar um
    identificador — é falha estrutural do documento (SourceSchemaError). Único
    ponto de verificação em `src/`: nenhum `isdigit()` solto."""
    return isinstance(value, str) and value.isascii() and value.isdigit()
