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


def patch_order_key(patch: str) -> tuple[int, int] | None:
    """Ordem de patch "major.minor" ("16.20" -> (16, 20)); qualquer outra forma
    (DEMO-1, "16", "16.x", dígitos não ASCII) -> None, que nunca entra em comparação."""
    parts = patch.split(".")
    if len(parts) != 2 or not all(is_ascii_digits(part) for part in parts):
        return None
    return int(parts[0]), int(parts[1])
