"""Inventário oficial pós-partida, isolado dos estados anteriores à compra."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from riftlab.shared.errors import SourceSchemaError
from riftlab.shared.numeric import is_ascii_digits


@dataclass(frozen=True, slots=True)
class FinalItems:
    items: tuple[int, ...]
    trinket_item_id: int | None = None
    role_bound_item_id: int | None = None


def parse_final_items(raw: dict[str, Any]) -> FinalItems | None:
    """Ausência integral é desconhecida; slots parciais ou inválidos são erro."""
    fields = [f"item{i}" for i in range(7)]
    if not any(key in raw for key in [*fields, "roleBoundItem"]):
        return None

    def item_id(value: Any) -> int:
        if isinstance(value, str) and is_ascii_digits(value):
            # Limita antes de converter, inclusive frente ao limite de dígitos do Python.
            if len(value) > 10:
                raise SourceSchemaError(code="MATCH_FINAL_ITEMS_INVALID", detail="item inválido")
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2_147_483_647:
            raise SourceSchemaError(code="MATCH_FINAL_ITEMS_INVALID", detail="item inválido")
        return value

    slots = [item_id(raw.get(key)) for key in fields]
    role = raw.get("roleBoundItem")
    return FinalItems(
        tuple(value for value in slots[:6] if value),
        slots[6] or None,
        (item_id(role) or None) if role is not None else None,
    )
