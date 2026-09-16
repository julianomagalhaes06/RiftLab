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
excluído em cascata, motivo explícito, nunca reescrito para caber (v3).

Revisão (v4): validação explícita reforçada em toda leitura de container —
chaves de `from`/`maps` precisam ser strings numéricas de verdade antes de
`int()` (nunca deixa `ValueError` cru escapar); valores de `maps` precisam
ser booleanos de verdade (um valor não-booleano nunca é tratado como
"mapa=false" por acidente — isso confundiria inválido com ausência
legítima); valores de `stats` precisam ser número finito, nunca bool nem
string; `name` de tipo errado (não string) é falha estrutural — só uma
string vazia (tipo certo, valor legitimamente ausente, achado real de
rede) é excluída como artefato; item_id duplicado após normalização (ex.:
chaves "007" e "7" colapsando no mesmo inteiro) é rejeitado explicitamente."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

from riftlab.catalog.domain import CatalogItemSourceDTO, ExcludedCatalogItem
from riftlab.shared.errors import SourceSchemaError

CURATION_RULES_VERSION = "data_dragon_v4"

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


def _parse_legal_maps(maps_field: Any, item_id: int) -> list[int]:
    if not isinstance(maps_field, dict):
        raise SourceSchemaError(
            code="CATALOG_SOURCE_MISSING_FIELD", detail=f"item {item_id} sem campo maps"
        )
    legal_maps: list[int] = []
    for map_id, legal in maps_field.items():
        if not isinstance(map_id, str) or not map_id.isdigit():
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=f"item {item_id} com chave de maps não numérica: {map_id!r}",
            )
        # bool de verdade, nunca um valor truthy qualquer (revisão: "yes"
        # não pode virar false silenciosamente — inválido e ausência
        # legítima são coisas diferentes).
        if not isinstance(legal, bool):
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=f"item {item_id} com valor de maps[{map_id!r}] não booleano: {legal!r}",
            )
        if legal:
            legal_maps.append(int(map_id))
    return sorted(legal_maps)


def _parse_tags(tags: Any, item_id: int) -> list[str]:
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise SourceSchemaError(
            code="CATALOG_SOURCE_MISSING_FIELD", detail=f"item {item_id} tags inválidas"
        )
    return tags


def _parse_stats(stats: Any, item_id: int) -> dict[str, float]:
    if not isinstance(stats, dict):
        raise SourceSchemaError(
            code="CATALOG_SOURCE_MISSING_FIELD", detail=f"item {item_id} stats inválido"
        )
    for stat_key, stat_value in stats.items():
        if not isinstance(stat_key, str) or not stat_key:
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=f"item {item_id} com chave de stats inválida: {stat_key!r}",
            )
        if (
            isinstance(stat_value, bool)
            or not isinstance(stat_value, (int, float))
            or not math.isfinite(stat_value)
        ):
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=(
                    f"item {item_id} com stats[{stat_key!r}] não numérico finito: {stat_value!r}"
                ),
            )
    return stats


def _parse_recipe(raw_from: Any, item_id: int) -> list[dict[str, int]]:
    if not isinstance(raw_from, list):
        raise SourceSchemaError(
            code="CATALOG_SOURCE_MISSING_FIELD", detail=f"item {item_id} campo from inválido"
        )
    for component in raw_from:
        if not isinstance(component, str) or not component.isdigit():
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=f"item {item_id} com componente de from inválido: {component!r}",
            )
    component_counts = Counter(raw_from)
    return [
        {"itemId": int(component_id), "quantity": quantity}
        for component_id, quantity in sorted(component_counts.items(), key=lambda kv: kv[0])
    ]


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
    seen_ids: set[int] = set()

    for raw_item_id, raw_item in raw_items.items():
        if not isinstance(raw_item_id, str) or not raw_item_id.isdigit():
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=f"chave de item não é um item_id numérico: {raw_item_id!r}",
            )
        item_id = int(raw_item_id)
        # Duas chaves brutas distintas podem colapsar no mesmo inteiro após
        # normalização (ex.: "007" e "7") — rejeitado explicitamente, nunca
        # a segunda sobrescreve a primeira silenciosamente.
        if item_id in seen_ids:
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=f"item_id {item_id} duplicado após normalização da chave bruta",
            )
        seen_ids.add(item_id)
        if not isinstance(raw_item, dict):
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD", detail=f"item {item_id} não é um objeto"
            )

        # Achado real (Data Dragon 16.17.1, rede pública, fora da CI):
        # alguns itens legítimos do payload real têm name="" (ex.: item
        # 2008, inStore=false, sem mapa legal algum) — artefatos internos/
        # legados do provedor, não um schema quebrado. Distinção deliberada
        # (revisão): tipo errado (não string, inclusive ausente) é falha
        # estrutural do documento; só uma string VAZIA (tipo certo, valor
        # legitimamente ausente) é excluída como artefato conhecido — nunca
        # tratamos as duas situações como a mesma coisa.
        name = raw_item.get("name")
        if not isinstance(name, str):
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=f"item {item_id} com name de tipo inválido: {type(name).__name__}",
            )
        if not name:
            excluded.append(
                ExcludedCatalogItem(
                    item_id=item_id,
                    reason_code=_REASON_MISSING_NAME,
                    detail="item sem name (string vazia) — artefato não curável",
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

        legal_maps = _parse_legal_maps(raw_item.get("maps"), item_id)
        tags = _parse_tags(raw_item.get("tags", []), item_id)
        stats = _parse_stats(raw_item.get("stats", {}), item_id)

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

        recipe = _parse_recipe(raw_item.get("from", []), item_id)

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
