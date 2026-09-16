"""Curadoria de itens reais do Data Dragon (CC-202): separa normalização de
campos documentados (gold/from/maps/stats/tags) de decisões de curadoria
(quais classes de item são reconciliáveis, quais ficam explicitamente não
suportadas). Nunca deduz passivas complexas só por descrição/texto — texto
livre (description/plaintext/colloq) nunca vira regra. Ausência de campo
nunca vira zero/falso silenciosamente: cada campo consumido é validado.

Payload real inspecionado nesta sessão (Data Dragon 16.17.1, CDN público,
sem coleta de partidas) para confirmar o schema antes de generalizar:
`from` é uma lista de item_id (string) que PODE repetir o mesmo componente
(ex.: Last Whisper = ["1036","1036"], duas Adagas Ceifadoras) — multiset
real, nunca um conjunto; `specialRecipe` (27 itens reais, ex.: upgrades
Ornn/Muramana) referencia uma receita especial por identificador numérico
que não é uma lista de componentes reconciliável do mesmo jeito — excluída
explicitamente aqui, nunca forçada a caber no formato normal. Achado real
adicional (smoke de rede real contra 16.16.1/16.17.1, fora da CI): 4 itens
reais têm name="" (ex.: item 2008, inStore=false, nenhum mapa legal) —
artefatos internos/legados do provedor, nunca um nome inventado nem uma
falha do catálogo inteiro; excluídos com motivo (v2 da curadoria).

Achado real adicional (mesmo smoke): itens normais (from numérico, sem
specialRecipe próprio) podem referenciar como componente um item que É
excluído por specialRecipe — ex.: os 5 upgrades míticos de "Bounty of
Worlds" (3867, excluído por specialRecipe) declaram from=["3867"]. Uma
referência para um item excluído nunca é tratada como "inexistente" (erro
de catálogo inteiro) nem como componente normal (reconciliação impossível,
o item base não tem preço/receita numérica real) — o item dependente é
excluído em cascata, motivo explícito, nunca reescrito para caber (v3)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from riftlab.catalog.domain import CatalogItemSourceDTO, ExcludedCatalogItem
from riftlab.shared.errors import SourceSchemaError

CURATION_RULES_VERSION = "data_dragon_v3"

_REASON_SPECIAL_RECIPE = "SPECIAL_RECIPE_UNSUPPORTED"
_REASON_MISSING_NAME = "MISSING_NAME_UNSUPPORTED"
_REASON_COMPONENT_UNSUPPORTED = "COMPONENT_UNSUPPORTED"


@dataclass(slots=True)
class _Candidate:
    item_id: int
    price: int
    recipe: list[dict[str, int]]
    attributes: dict[str, Any]
    flags: dict[str, Any]


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SourceSchemaError(
            code="CATALOG_SOURCE_MISSING_FIELD", detail=f"{field} não é um inteiro válido"
        )
    return value


def _require_non_negative_int(value: Any, field: str) -> int:
    parsed = _require_int(value, field)
    if parsed < 0:
        raise SourceSchemaError(
            code="CATALOG_SOURCE_MISSING_FIELD", detail=f"{field} não pode ser negativo"
        )
    return parsed


def curate_data_dragon_items(
    raw_items: Any,
) -> tuple[tuple[CatalogItemSourceDTO, ...], tuple[ExcludedCatalogItem, ...]]:
    """raw_items é o dict `data` de item.json real (chave = item_id como
    string) — tipado Any porque o formato ainda não foi validado quando
    chega aqui (pode ser None/lista/qualquer coisa vinda de JSON externo);
    validado explicitamente abaixo, nunca confiado pelo tipo estático.
    Retorna itens curados e itens explicitamente excluídos com motivo —
    nunca lança por uma classe de item legítima ainda não suportada
    (specialRecipe), só a exclui e registra a causa."""
    if not isinstance(raw_items, dict) or not raw_items:
        raise SourceSchemaError(
            code="CATALOG_SOURCE_MISSING_FIELD", detail="item.json sem campo 'data' com itens"
        )

    candidates: dict[int, _Candidate] = {}
    excluded: list[ExcludedCatalogItem] = []

    for raw_item_id, raw_item in raw_items.items():
        if not isinstance(raw_item_id, str) or not raw_item_id.isdigit():
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=f"chave de item não é um item_id numérico: {raw_item_id!r}",
            )
        item_id = int(raw_item_id)
        if not isinstance(raw_item, dict):
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD", detail=f"item {item_id} não é um objeto"
            )

        # Achado real (Data Dragon 16.17.1, rede pública, fora da CI):
        # alguns itens legítimos do payload real têm name="" (ex.: item
        # 2008, inStore=false, sem mapa legal algum) — artefatos internos/
        # legados do provedor, não um schema quebrado. Nunca inventamos um
        # nome nem tratamos isso como falha do catálogo inteiro: excluído
        # com motivo explícito, mesma política de specialRecipe.
        name = raw_item.get("name")
        if not isinstance(name, str) or not name:
            excluded.append(
                ExcludedCatalogItem(
                    item_id=item_id,
                    reason_code=_REASON_MISSING_NAME,
                    detail="item sem name (string vazia ou ausente) — artefato não curável",
                )
            )
            continue

        gold = raw_item.get("gold")
        if not isinstance(gold, dict):
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD", detail=f"item {item_id} sem campo gold"
            )
        price = _require_non_negative_int(gold.get("total"), f"item {item_id} gold.total")
        purchasable = gold.get("purchasable")
        if not isinstance(purchasable, bool):
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=f"item {item_id} gold.purchasable não é booleano",
            )

        maps_field = raw_item.get("maps")
        if not isinstance(maps_field, dict):
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD", detail=f"item {item_id} sem campo maps"
            )
        legal_maps = sorted(int(map_id) for map_id, legal in maps_field.items() if legal is True)

        tags = raw_item.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD", detail=f"item {item_id} tags inválidas"
            )

        stats = raw_item.get("stats", {})
        if not isinstance(stats, dict):
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD", detail=f"item {item_id} stats inválido"
            )

        # Itens com specialRecipe (ex.: upgrades Ornn, Muramana) não têm uma
        # lista de componentes reconciliável do mesmo jeito que o resto do
        # catálogo — política explícita: excluído com motivo, nunca
        # forçado a caber no formato normal de receita nem descartado sem
        # registro (revisão do card: "excluir/qualificar com motivo
        # explícito quando pertinente").
        if "specialRecipe" in raw_item:
            excluded.append(
                ExcludedCatalogItem(
                    item_id=item_id,
                    reason_code=_REASON_SPECIAL_RECIPE,
                    detail="item com specialRecipe (receita especial não numérica/reconciliável)",
                )
            )
            continue

        raw_from = raw_item.get("from", [])
        if not isinstance(raw_from, list) or not all(isinstance(c, str) for c in raw_from):
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD", detail=f"item {item_id} campo from inválido"
            )
        component_counts = Counter(raw_from)
        recipe = [
            {"itemId": int(component_id), "quantity": quantity}
            for component_id, quantity in sorted(component_counts.items(), key=lambda kv: kv[0])
        ]

        flags: dict[str, Any] = {"purchasable": purchasable, "maps": legal_maps}
        required_champion = raw_item.get("requiredChampion")
        if isinstance(required_champion, str) and required_champion:
            flags["requiredChampion"] = required_champion
        if raw_item.get("consumed") is True:
            flags["consumed"] = True
            if isinstance(raw_item.get("stacks"), int):
                flags["stacks"] = raw_item["stacks"]

        candidates[item_id] = _Candidate(
            item_id=item_id,
            price=price,
            recipe=recipe,
            attributes={"name": name, "tags": sorted(tags), "stats": stats},
            flags=flags,
        )

    # Cascata (achado real, ver docstring do módulo): um candidato cuja
    # receita referencia um item já excluído (specialRecipe/sem nome, ou um
    # candidato excluído numa rodada anterior desta cascata) nunca é tratado
    # como referência inexistente nem como componente normal — é excluído
    # também, com motivo explícito. Ponto fixo: repete até uma rodada não
    # excluir mais nada (cadeias de upgrade têm poucos níveis na prática,
    # mas o algoritmo não presume um teto).
    excluded_ids = {item.item_id for item in excluded}
    changed = True
    while changed:
        changed = False
        for item_id, candidate in list(candidates.items()):
            unsupported = [
                component["itemId"]
                for component in candidate.recipe
                if component["itemId"] in excluded_ids
            ]
            if unsupported:
                del candidates[item_id]
                excluded_ids.add(item_id)
                excluded.append(
                    ExcludedCatalogItem(
                        item_id=item_id,
                        reason_code=_REASON_COMPONENT_UNSUPPORTED,
                        detail=(
                            "receita referencia componente(s) excluído(s) da curadoria "
                            f"(não reconciliável numericamente): {sorted(set(unsupported))}"
                        ),
                    )
                )
                changed = True

    items = tuple(
        CatalogItemSourceDTO(
            item_id=c.item_id,
            price=c.price,
            recipe=c.recipe,
            attributes=c.attributes,
            flags=c.flags,
        )
        for c in sorted(candidates.values(), key=lambda c: c.item_id)
    )
    return items, tuple(sorted(excluded, key=lambda x: x.item_id))
