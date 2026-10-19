"""Referência da revisão da partida (CC-1003): compras e ordem de habilidades de jogadores do
mesmo campeão e rota num patch, contadas offline pelo mesmo caminho do produto.

Popularidade não é qualidade: o rótulo diz se a escolha é comum entre jogadores parecidos,
nunca se foi certa ou errada.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

MIN_REFERENCE_SUPPORT = 20
COMMON_MAX_RANK = 3
COMMON_MIN_SHARE = 0.10
ALTERNATIVE_MIN_SHARE = 0.03
SKILL_ALTERNATIVE_MIN_SHARE = 0.15
# Sub-motivos de recorte que não impedem a revisão: ela vale para qualquer campeão, rota,
# fila e patch, até o 5º item principal. Mapa e rota desconhecidos continuam impedindo.
_REVIEW_SCOPE_SUBREASONS = frozenset(
    {
        "QUEUE_OUT_OF_SCOPE",
        "PATCH_OUT_OF_SCOPE",
        "ROLE_OUT_OF_SCOPE",
        "CHAMPION_OUT_OF_SCOPE",
        "MAIN_ITEM_ORDINAL_BEYOND_SECOND",
    }
)
_SKILL_LETTERS = {1: "Q", 2: "W", 3: "E"}
_SKILL_MAX_RANK = 5

PurchaseLabel = Literal["COMMON", "ALTERNATIVE", "UNCOMMON", "NO_REFERENCE"]
SkillLabel = Literal["MOST_COMMON", "ALTERNATIVE", "UNCOMMON", "NO_REFERENCE"]


def is_review_ready(reasons: Iterable[Mapping[str, Any]]) -> bool:
    """Pronta para a revisão: nenhum motivo além do recorte (campeão, rota, fila, patch, etapa)."""
    for reason in reasons:
        if reason.get("code") != "OUT_OF_SCOPE":
            return False
        if not set(reason.get("subReasons") or ()) <= _REVIEW_SCOPE_SUBREASONS:
            return False
    return True


def skill_priority(skill_ups: Sequence[tuple[Any, Any]]) -> str | None:
    """Ordem em que Q, W e E chegam ao nível máximo (subidas NORMAL, em ordem). Habilidade que
    não chegou fica depois, pela quantidade de pontos. None se nenhuma chegou (partida curta)."""
    points = {slot: 0 for slot in _SKILL_LETTERS}
    reached: dict[int, int] = {}
    for index, (slot, kind) in enumerate(skill_ups):
        if kind not in (None, "NORMAL") or slot not in _SKILL_LETTERS:
            continue
        points[slot] += 1
        if points[slot] == _SKILL_MAX_RANK:
            reached.setdefault(slot, index)
    if not reached:
        return None
    order = sorted(_SKILL_LETTERS, key=lambda s: (reached.get(s, 10_000), -points[s], s))
    return ">".join(_SKILL_LETTERS[slot] for slot in order)


@dataclass(frozen=True, slots=True)
class ReferenceShare:
    item_id: int
    count: int


@dataclass(frozen=True, slots=True)
class PurchaseCell:
    n: int
    items: tuple[ReferenceShare, ...]

    def share(self, item_id: int) -> tuple[float, int | None]:
        for rank, entry in enumerate(self.items, start=1):
            if entry.item_id == item_id:
                return entry.count / self.n, rank
        return 0.0, None


@dataclass(frozen=True, slots=True)
class MatchReviewReference:
    reference_version: str
    patch: str
    queue_id: int
    data_dragon_version: str
    purchases: Mapping[str, PurchaseCell]
    skills: Mapping[str, tuple[int, tuple[tuple[str, int], ...]]]

    def purchase_cell(self, champion_id: int, role: str, ordinal: int) -> PurchaseCell | None:
        return self.purchases.get(f"{champion_id}:{role}:{ordinal}")

    def label_purchase(
        self, champion_id: int, role: str, ordinal: int, item_id: int
    ) -> tuple[PurchaseLabel, float | None, int | None, PurchaseCell | None]:
        cell = self.purchase_cell(champion_id, role, ordinal)
        if cell is None or cell.n < MIN_REFERENCE_SUPPORT:
            return "NO_REFERENCE", None, None, cell
        share, rank = cell.share(item_id)
        if rank is not None and rank <= COMMON_MAX_RANK and share >= COMMON_MIN_SHARE:
            return "COMMON", share, rank, cell
        if share >= ALTERNATIVE_MIN_SHARE:
            return "ALTERNATIVE", share, rank, cell
        return "UNCOMMON", share, rank, cell

    def label_skills(
        self, champion_id: int, role: str, priority: str | None
    ) -> tuple[SkillLabel, float | None, tuple[tuple[str, int], ...], int]:
        n, orders = self.skills.get(f"{champion_id}:{role}", (0, ()))
        if priority is None or n < MIN_REFERENCE_SUPPORT:
            return "NO_REFERENCE", None, orders, n
        share = next((count for order, count in orders if order == priority), 0) / n
        if orders and orders[0][0] == priority:
            return "MOST_COMMON", share, orders, n
        if share >= SKILL_ALTERNATIVE_MIN_SHARE:
            return "ALTERNATIVE", share, orders, n
        return "UNCOMMON", share, orders, n


def parse_reference(document: Mapping[str, Any]) -> MatchReviewReference:
    if document.get("schemaVersion") != "match_review_reference_v1":
        raise ValueError("referência da revisão com schema desconhecido")
    purchases = {
        key: PurchaseCell(
            int(cell["n"]),
            tuple(ReferenceShare(int(item), int(count)) for item, count in cell["items"]),
        )
        for key, cell in document["purchases"].items()
    }
    skills = {
        key: (int(cell["n"]), tuple((str(p), int(c)) for p, c in cell["priorities"]))
        for key, cell in document["skills"].items()
    }
    return MatchReviewReference(
        str(document["referenceVersion"]),
        str(document["patch"]),
        int(document["queueId"]),
        str(document["source"]["dataDragonVersion"]),
        purchases,
        skills,
    )


def load_reference(path: Path) -> MatchReviewReference | None:
    if not path.exists():
        return None
    return parse_reference(json.loads(path.read_text(encoding="utf-8")))
