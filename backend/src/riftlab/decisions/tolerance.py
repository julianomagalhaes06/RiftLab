"""Incerteza tolerada do inventário (CC-1502): só itens que não mudam candidatos principais.

A reconstrução marca o estado inteiro como incerto ao primeiro evento sem explicação. No corpus
16.19 quase toda a perda do meio vem de botas destruídas sem posse (missão de rota) e a do
suporte, de Sentinelas de Controle. Bota, consumível e trinket não entram em receita de item
principal nem em grupo exclusivo de item principal: a dúvida sobre eles não muda quais itens
principais são legais nem quanto falta para cada um. Só muda a contagem de espaços, e por isso
eles saem do inventário usado para os candidatos e a decisão fica marcada.

Tolerância só vale quando TODOS os motivos pré-decisão trazem o item e esse item é bota,
consumível ou trinket. Qualquer outro motivo (sem item, item principal, componente, item
desconhecido) mantém a decisão inelegível. A validade da tolerância é medida contra os itens
finais oficiais antes de entrar no produto (relatório do CC-1502)."""

from __future__ import annotations

from dataclasses import dataclass, replace

from riftlab.catalog.classification import ItemClass, ItemClassificationTable
from riftlab.decisions.features import PreDecisionView
from riftlab.decisions.selection import LINEAGE_IDENTITY

TOLERANCE_VERSION = "inventory_tolerance_v2"  # v2: cadeia do item de suporte pela linhagem
TOLERABLE_CLASSES = frozenset(
    {ItemClass.BOOTS, ItemClass.CONSUMABLE, ItemClass.TRINKET, ItemClass.NOT_PURCHASABLE}
)
TOLERATED_CODE = "INVENTORY_TOLERATED_NON_MAIN"


@dataclass(frozen=True, slots=True)
class ToleratedView:
    view: PreDecisionView
    tolerated: bool
    removed_item_ids: tuple[int, ...] = ()


def _lineage(items: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    """Etapas da cadeia do item de suporte (e resultados de transformação) contam como o item de
    origem, classificável e fora de qualquer receita de item principal: o espaço continua
    ocupado e nenhum candidato principal muda."""
    merged: dict[int, int] = {}
    for item_id, count in items:
        identity = LINEAGE_IDENTITY.get(item_id, item_id)
        merged[identity] = merged.get(identity, 0) + count
    return tuple(sorted(merged.items()))


def tolerate(
    view: PreDecisionView, classification: ItemClassificationTable
) -> ToleratedView | None:
    """A visão (com a linhagem aplicada) quando não há incerteza; a visão sem os itens duvidosos
    quando toda a incerteza é tolerável; None quando a incerteza alcança algo que importa."""
    normalized = _lineage(view.inventory_items)
    if not view.pre_decision_uncertain and not view.pre_decision_reasons:
        if normalized == view.inventory_items:
            return ToleratedView(view, False)
        return ToleratedView(replace(view, inventory_items=normalized), False)
    if not view.pre_decision_reasons:
        return None  # incerto sem motivo identificado
    doubtful: set[int] = set()
    for reason in view.pre_decision_reasons:
        if reason.item_id is None:
            return None
        if classification.classify(reason.item_id).item_class not in TOLERABLE_CLASSES:
            return None
        doubtful.add(reason.item_id)
    inventory = tuple((i, n) for i, n in normalized if i not in doubtful)
    if any(
        classification.classify(i).item_class
        in (ItemClass.UNKNOWN, ItemClass.EXCLUDED, ItemClass.UNCLASSIFIED)
        for i, _ in inventory
    ):
        return None
    trinket = None if view.trinket_item_id in doubtful else view.trinket_item_id
    cleaned = replace(
        view,
        inventory_items=inventory,
        trinket_item_id=trinket,
        pre_decision_uncertain=False,
        pre_decision_reasons=(),
    )
    return ToleratedView(cleaned, True, tuple(sorted(doubtful)))


__all__ = ["TOLERANCE_VERSION", "TOLERATED_CODE", "ToleratedView", "tolerate"]
