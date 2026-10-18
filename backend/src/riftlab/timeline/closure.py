"""G-FINAL dos slots normais e trinket; funções puras, sem liberar regras empíricas."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from riftlab.catalog.domain import Item
from riftlab.ingestion.final_items import FinalItems
from riftlab.timeline.application import InventorySnapshot


@dataclass(frozen=True, slots=True)
class ClosureDetail:
    extra: dict[int, int]
    missing: dict[int, int]
    trinket_ok: bool

    @property
    def failed(self) -> bool:
        return bool(self.extra or self.missing or not self.trinket_ok)


def closure_detail(
    predicted: InventorySnapshot,
    official: FinalItems,
    catalog: Mapping[int, Item],
) -> ClosureDetail:
    # O slot de missão é pós-partida, separado dos slots normais modelados.
    # Concessões não modeladas são excluídas de ambos os lados pelo protocolo.
    granted = {2010, 2422, 2150, 2151, 2152}
    reported = Counter(item for item in official.items if item not in granted)
    counts = {item: count for item, count in predicted.items if item not in granted}
    extra, missing = {}, {}
    for item_id in sorted(set(reported) | set(counts)):
        actual, expected = counts.get(item_id, 0), reported.get(item_id, 0)
        item = catalog.get(item_id)
        if item is not None and item.flags.get("consumed"):
            actual, expected = int(actual > 0), int(expected > 0)
        if actual > expected:
            extra[item_id] = actual - expected
        elif expected > actual:
            missing[item_id] = expected - actual
    return ClosureDetail(
        extra, missing, (predicted.trinket_item_id or 0) == (official.trinket_item_id or 0)
    )


def verdict(clean: int, failures: int, matches: int) -> tuple[str, float | None]:
    if min(clean, failures, matches) < 0 or failures > clean or matches > clean:
        raise ValueError("contagens de fechamento inválidas")
    if not clean:
        return "NO_SAMPLE", 1.0
    z = 1.96
    p = failures / clean
    upper = (
        p + z * z / (2 * clean) + z * math.sqrt(p * (1 - p) / clean + z * z / (4 * clean * clean))
    ) / (1 + z * z / clean)
    return (
        "NO_SAMPLE" if clean < 20 or matches < 3 else "ACCEPT" if upper <= 0.10 else "REJECT"
    ), upper
