"""Bateria DETERMINÍSTICA de cenários para o reducer de reconstrução (CC-207).

Serve a dois testes: (1) regressão v6 BIT A BIT com as regras empíricas desligadas (os
digests em `tests/unit/timeline/golden/reducer_v6_digests.json` foram gerados pelo
reducer v6 do HEAD 2575e8a, ANTES de a v7 existir); (2) propriedades metamórficas
(pré-decisão independente do alvo; invariância do prefixo) com as regras LIGADAS. Sem
aleatoriedade da biblioteca padrão: LCG próprio, estável em qualquer versão do Python.
Nenhum dado real: são sequências sintéticas."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from riftlab.catalog.domain import Item
from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.domain import TimelineEvent

CID = uuid.UUID("00000000-0000-0000-0000-000000000207")
P1, P6 = 1, 6
A, B, IE, ONE_A = 1038, 1037, 3031, 3002
POTION, WARD = 2003, 2055
TRK_A, TRK_B, TRK_C = 3340, 3363, 3364
BOOTS, BOOTS2 = 1001, 3020
QUEST, RECALL, SYSTEM_ONLY = 1202, 2001, 2002
SYSTEM_IDS = frozenset({QUEST, RECALL, SYSTEM_ONLY})
BIG = 3089  # composto de 3 níveis: A + B + ONE_A


@dataclass(frozen=True, slots=True)
class Frame:
    timestamp_ms: int
    participant_frames: dict[int, dict[str, Any]] = field(default_factory=dict)


def _item(
    item_id: int, recipe: tuple[int, ...] = (), tags: tuple[str, ...] = (), **flags: Any
) -> Item:
    return Item(
        CID,
        item_id,
        100,
        [{"itemId": c, "quantity": 1} for c in recipe],
        {"tags": list(tags), "into": []},
        dict(flags),
    )


CATALOG: dict[int, Item] = {
    A: _item(A),
    B: _item(B),
    IE: _item(IE, (A, B)),
    ONE_A: _item(ONE_A, (A,)),
    BIG: _item(BIG, (IE, ONE_A)),
    POTION: _item(POTION, tags=("Consumable",), consumed=True, stacks=5),
    WARD: _item(WARD, tags=("Consumable",), consumed=True, stacks=2),
    TRK_A: _item(TRK_A, tags=("Trinket",)),
    TRK_B: _item(TRK_B, tags=("Trinket",)),
    TRK_C: _item(TRK_C, tags=("Trinket",)),
    BOOTS: _item(BOOTS),
    BOOTS2: _item(BOOTS2, (BOOTS,)),
    QUEST: _item(QUEST),
    RECALL: _item(RECALL),
    SYSTEM_ONLY: _item(SYSTEM_ONLY),
}
FRAMES = [Frame(0, {P1: {"currentGold": 700, "level": 3}, P6: {"currentGold": 400, "level": 2}})]


def ev(index: int, ts: int, type_: str, participant: int = P1, **payload: Any) -> TimelineEvent:
    return TimelineEvent("M", index, ts, type_, {"participantId": participant, **payload})


def buy(i: int, ts: int, item: int, p: int = P1) -> TimelineEvent:
    return ev(i, ts, "ITEM_PURCHASED", p, itemId=item)


def destroy(i: int, ts: int, item: int, p: int = P1) -> TimelineEvent:
    return ev(i, ts, "ITEM_DESTROYED", p, itemId=item)


def sell(i: int, ts: int, item: int, p: int = P1) -> TimelineEvent:
    return ev(i, ts, "ITEM_SOLD", p, itemId=item)


def undo(i: int, ts: int, before: int, after: int, p: int = P1) -> TimelineEvent:
    return ev(i, ts, "ITEM_UNDO", p, beforeId=before, afterId=after, goldGain=0)


def noise(i: int, ts: int) -> TimelineEvent:
    return TimelineEvent("M", i, ts, "CHAMPION_KILL", {"killerId": 2})


def hand_written() -> dict[str, list[TimelineEvent]]:
    """Formas reais e reprovadas de cada mecânica (mesmo T = grupo co-temporal)."""
    T = 60_000
    return {
        "transacao_real_destroy_antes_da_compra": [
            buy(0, 1000, A),
            buy(1, 2000, B),
            destroy(2, T, A),
            destroy(3, T, B),
            buy(4, T, IE),
        ],
        "compra_composta_sem_destroys": [buy(0, 1000, A), buy(1, 2000, B), buy(2, T, IE)],
        "destroy_depois_da_compra_forma_reprovada": [
            buy(0, 1000, A),
            buy(1, 2000, B),
            buy(2, T, IE),
            destroy(3, T, A),
            destroy(4, T, B),
        ],
        "excesso_de_destroys_do_componente": [
            buy(0, 1000, A),
            buy(1, 1500, A),
            destroy(2, T, A),
            destroy(3, T, A),
            buy(4, T, ONE_A),
        ],
        "composto_de_tres_niveis": [
            buy(0, 500, A),
            buy(1, 800, B),
            buy(2, 900, ONE_A),
            destroy(3, T, A),
            destroy(4, T, B),
            destroy(5, T, ONE_A),
            buy(6, T, BIG),
        ],
        "pocao_comprada_e_consumida": [
            buy(0, 1000, POTION),
            destroy(1, 20_000, POTION),
            buy(2, 30_000, A),
        ],
        "pocao_consumida_sem_posse": [destroy(0, 20_000, POTION), buy(1, 30_000, A)],
        "duas_pocoes_uma_consumida": [
            buy(0, 1000, POTION),
            buy(1, 1100, POTION),
            destroy(2, 20_000, POTION),
            buy(3, 30_000, A),
        ],
        "consumivel_reivindicado_pela_receita_forma_normal": [
            buy(0, 1000, A),
            destroy(1, T, A),
            buy(2, T, ONE_A),
        ],
        "troca_de_trinket_destroy_antes_da_compra": [
            buy(0, 1000, TRK_A),
            destroy(1, 60_000, TRK_A),
            buy(2, 60_000, TRK_B),
            buy(3, 90_000, A),
        ],
        "troca_do_trinket_inicial_implicito": [
            destroy(0, 60_000, TRK_A),
            buy(1, 60_000, TRK_B),
            buy(2, 90_000, A),
        ],
        "substituicao_inicial_sem_destroy": [buy(0, 8_000, TRK_C), buy(1, 90_000, A)],
        "destroy_de_trinket_sem_compra_de_trinket": [
            buy(0, 1000, TRK_A),
            destroy(1, 60_000, TRK_A),
            buy(2, 90_000, A),
        ],
        "destroy_de_trinket_de_outro_item": [
            buy(0, 1000, TRK_A),
            destroy(1, 60_000, TRK_B),
            buy(2, 60_000, TRK_C),
            buy(3, 90_000, A),
        ],
        "item_de_sistema_sem_posse": [
            destroy(0, 30_000, QUEST),
            destroy(1, 40_000, RECALL),
            buy(2, 50_000, A),
        ],
        "item_de_sistema_comprado_e_destruido": [
            buy(0, 1000, QUEST),
            destroy(1, 30_000, QUEST),
            buy(2, 50_000, A),
        ],
        "item_fora_da_lista_sem_posse": [destroy(0, 30_000, A), buy(1, 50_000, B)],
        "undo_simples_de_compra": [
            buy(0, 1000, A),
            buy(1, 2000, B),
            undo(2, 2500, B, 0),
            buy(3, 5000, ONE_A),
        ],
        "undo_de_dois_niveis": [
            buy(0, 1000, A),
            buy(1, 1100, B),
            undo(2, 1200, B, 0),
            undo(3, 1300, A, 0),
            buy(4, 5000, ONE_A),
        ],
        "undo_de_transacao_composta_e_venda_anterior": [
            buy(0, 500, A),
            buy(1, 600, B),
            sell(2, 700, A),
            destroy(3, T, B),
            buy(4, T, ONE_A),
            undo(5, T + 1000, ONE_A, 0),
            undo(6, T + 2000, 0, A),
            buy(7, T + 3000, A),
        ],
        "undo_sem_correlacao": [buy(0, 1000, A), undo(1, 1500, B, 0), buy(2, 5000, B)],
        "undo_de_venda": [buy(0, 1000, A), sell(1, 2000, A), undo(2, 2500, 0, A), buy(3, 5000, B)],
        "venda_sem_posse": [sell(0, 1000, A), buy(1, 2000, B)],
        "botas_e_troca_de_trinket_no_mesmo_jogo": [
            buy(0, 3000, TRK_A),
            buy(1, 3100, POTION),
            destroy(2, 30_000, POTION),
            buy(3, 100_000, BOOTS),
            destroy(4, 200_000, TRK_A),
            buy(5, 200_000, TRK_C),
            destroy(6, 300_000, BOOTS),
            buy(7, 300_000, BOOTS2),
        ],
        "eventos_de_outro_participante_e_ruido": [
            buy(0, 1000, A),
            buy(1, 1100, B, P6),
            noise(2, 1200),
            destroy(3, 1300, B, P6),
            buy(4, 5000, ONE_A),
        ],
    }


class Lcg:
    """LCG de 64 bits (Numerical Recipes): determinístico em qualquer Python."""

    def __init__(self, seed: int) -> None:
        self.state = (seed * 6364136223846793005 + 1442695040888963407) % (1 << 64)

    def below(self, n: int) -> int:
        self.state = (self.state * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (self.state >> 33) % n


_ITEMS = [
    A,
    B,
    IE,
    ONE_A,
    BIG,
    POTION,
    WARD,
    TRK_A,
    TRK_B,
    TRK_C,
    BOOTS,
    BOOTS2,
    QUEST,
    RECALL,
    SYSTEM_ONLY,
]


def random_stream(seed: int) -> list[TimelineEvent]:
    rng = Lcg(seed)
    events: list[TimelineEvent] = []
    ts = 1000
    for index in range(4 + rng.below(11)):
        if rng.below(100) >= 45:  # 45%: repete o instante (grupo co-temporal)
            ts += 1000 + 1000 * rng.below(60)
        kind = rng.below(100)
        item = _ITEMS[rng.below(len(_ITEMS))]
        p = P6 if rng.below(100) < 8 else P1
        if kind < 46:
            events.append(buy(index, ts, item, p))
        elif kind < 74:
            events.append(destroy(index, ts, item, p))
        elif kind < 82:
            events.append(sell(index, ts, item, p))
        elif kind < 96:
            events.append(
                undo(
                    index,
                    ts,
                    item if rng.below(2) else 0,
                    0 if rng.below(2) else _ITEMS[rng.below(len(_ITEMS))],
                    p,
                )
            )
        else:
            events.append(noise(index, ts))
    return events


def battery(n_random: int = 240) -> dict[str, list[TimelineEvent]]:
    scenarios = dict(hand_written())
    for seed in range(n_random):
        scenarios[f"aleatorio_{seed:03d}"] = random_stream(seed)
    return scenarios


def canonical(purchases: Any) -> str:
    return json.dumps(
        [dataclasses.asdict(p) for p in purchases],
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )


def digest(purchases: Any) -> str:
    return hashlib.sha256(canonical(purchases).encode()).hexdigest()


ORIGINS = {"REAL": SourceOrigin.COMPLETED_MATCH, "DEMO": SourceOrigin.CURATED_DEMO}
