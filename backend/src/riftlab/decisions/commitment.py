"""Compromisso de compra (CC-1501): quando a escolha de um item principal foi feita.

O evento `ITEM_PURCHASED` do item completo chega depois da escolha: na mediana do corpus 16.19,
68% do preço já estava pago em componentes nesse instante, e em 52% das compras nenhum outro item
aproveitava o ouro já pago. Este módulo, puro, separa as duas coisas:

- `assess_completion` classifica a compra no instante em que o item fica pronto, com os candidatos
  legais daquele inventário: FORCED (só o alvo aproveita todo o ouro já pago), SHARED (há rivais
  que aproveitam ao menos o mesmo ouro), OPEN (nada do alvo estava pago) ou UNKNOWN.
- `commitment_anchor` devolve o estado em que a escolha ainda estava aberta: a primeira compra
  depois do item principal anterior (ou a primeira da partida). Mesma âncora do planejamento do
  CC-407/CC-701, agora para qualquer ordem. Nunca consulta a identidade do alvo.
- `path_start` (só retrospectivo, para a revelação): a primeira compra, depois do item principal
  anterior, de um componente da árvore do alvo.

A classificação usa a compra histórica e por isso é retrospectiva: serve à explicação depois da
revelação e à avaliação, nunca às features nem ao que o modo Estudo mostra antes de revelar."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from riftlab.catalog.domain import Item
from riftlab.timeline.domain import DecisionState

COMMITMENT_VERSION = "commitment_v1"


class CompletionKind(StrEnum):
    FORCED = "FORCED"
    SHARED = "SHARED"
    OPEN = "OPEN"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CompletionAssessment:
    kind: CompletionKind
    price: int | None = None
    paid_gold: int | None = None
    rivals: tuple[int, ...] = ()

    @property
    def share_paid(self) -> float | None:
        if self.price is None or self.paid_gold is None or self.price <= 0:
            return None
        return self.paid_gold / self.price

    def to_payload(self) -> dict[str, Any]:
        share = self.share_paid
        return {
            "kind": self.kind.value,
            "price": self.price,
            "paidGold": self.paid_gold,
            "sharePaid": round(share, 4) if share is not None else None,
            "rivals": list(self.rivals),
            "version": COMMITMENT_VERSION,
        }


def assess_completion(
    target_item_id: int,
    candidates: Sequence[Any] | None,
    prices: Mapping[int, int],
) -> CompletionAssessment:
    """Classifica a compra do alvo com os candidatos legais do inventário ANTES dela.

    `candidates=None` significa que o inventário não permitia enumerar candidatos (incerto,
    item inclassificável): UNKNOWN. Um rival precisa aproveitar pelo menos o mesmo ouro já pago
    que o alvo; um rival que aproveita mais também conta (o jogador trocou de plano)."""
    if candidates is None:
        return CompletionAssessment(CompletionKind.UNKNOWN)
    remaining = {c.item_id: c.remaining_cost for c in candidates}
    price = prices.get(target_item_id)
    if target_item_id not in remaining or price is None:
        return CompletionAssessment(CompletionKind.UNKNOWN, price)
    paid = price - remaining[target_item_id]
    if paid <= 0:
        return CompletionAssessment(CompletionKind.OPEN, price, 0)
    rivals = tuple(
        sorted(
            item_id
            for item_id, cost in remaining.items()
            if item_id != target_item_id and item_id in prices and prices[item_id] - cost >= paid
        )
    )
    kind = CompletionKind.SHARED if rivals else CompletionKind.FORCED
    return CompletionAssessment(kind, price, paid, rivals)


def previous_main_index(
    states: Sequence[DecisionState],
    target_index: int,
    is_main_completion: Callable[[DecisionState], bool],
) -> int | None:
    """Índice de evento do último item principal completo antes do alvo (None = nenhum)."""
    earlier = [
        s.event_source_index
        for s in states
        if s.event_source_index < target_index and is_main_completion(s)
    ]
    return max(earlier) if earlier else None


def commitment_anchor(
    states: Sequence[DecisionState],
    *,
    target_index: int,
    previous_main: int | None,
) -> DecisionState | None:
    """Primeira compra depois do item principal anterior (ou a primeira da partida), nunca
    depois do alvo. Pode ser o próprio estado do alvo: o jogador foi à loja e comprou o item
    inteiro de uma vez, sem nada pago antes (compra OPEN)."""
    after = -1 if previous_main is None else previous_main
    later = sorted(
        (s for s in states if after < s.event_source_index <= target_index),
        key=lambda s: s.event_source_index,
    )
    return later[0] if later else None


def recipe_closure(item_id: int, items: Mapping[int, Item]) -> frozenset[int]:
    """Todos os componentes, diretos e indiretos, de um item do catálogo (sem o próprio)."""
    seen: set[int] = set()
    pending = [item_id]
    while pending:
        current = items.get(pending.pop())
        recipe = current.recipe if current is not None else None
        for entry in recipe if isinstance(recipe, list) else ():
            child = entry.get("itemId") if isinstance(entry, dict) else None
            if isinstance(child, int) and not isinstance(child, bool) and child not in seen:
                seen.add(child)
                pending.append(child)
    return frozenset(seen)


def _purchased(state: DecisionState) -> int | None:
    value = state.snapshot.get("purchasedItemId")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def path_start(
    states: Sequence[DecisionState],
    *,
    target_index: int,
    target_item_id: int,
    previous_main: int | None,
    items: Mapping[int, Item],
) -> DecisionState | None:
    """Primeira compra de um componente da árvore do alvo depois do item principal anterior.
    None quando os componentes já estavam no inventário antes disso."""
    tree = recipe_closure(target_item_id, items)
    after = -1 if previous_main is None else previous_main
    for state in sorted(states, key=lambda s: s.event_source_index):
        if after < state.event_source_index < target_index and _purchased(state) in tree:
            return state
    return None


__all__ = [
    "COMMITMENT_VERSION",
    "CompletionAssessment",
    "CompletionKind",
    "assess_completion",
    "commitment_anchor",
    "path_start",
    "previous_main_index",
    "recipe_closure",
]
