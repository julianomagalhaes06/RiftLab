"""Classificação de itens por catálogo (CC-204): decide, a partir dos campos
JÁ PERSISTIDOS de um catálogo versionado, se um item é um item PRINCIPAL
(completo, comprável no mapa alvo, nem bota, nem consumível, nem trinket) ou
outra coisa. Pura e versionada (`ITEM_CLASSIFICATION_VERSION`): sem I/O, sem
relógio, sem aleatoriedade. Qualquer alteração que mude o resultado exige
uma nova versão.

Por que o grafo de receita curado não basta (verificado nos catálogos
14.20.1 e 15.1.1 do Data Dragon público):
- "terminal no grafo curado" não significa principal: o item 2421 tem `into`
  no Data Dragon, mas o destino foi excluído da curadoria; no grafo curado ele
  pareceria terminal. Por isso a curadoria `data_dragon_v5` persiste `into`.
- botas de nível 3 (15.1.1: 3171, 3172) não têm a tag `Boots`; só a receita
  as liga a botas.
- `depth` não serve como regra: 3031 e 3089 têm depth 2 e são lendários.

Regras (fixas, na ordem; a primeira que casa decide) para a curadoria
`data_dragon_v5`:
  1. NOT_PURCHASABLE   flags.purchasable é false
  2. MAP_ILLEGAL       mapa 11 (Summoner's Rift) ausente de flags.maps
  3. CHAMPION_SPECIFIC flags.requiredChampion presente
  4. TRINKET           tag Trinket
  5. CONSUMABLE        tag Consumable ou flags.consumed verdadeiro
  6. BOOTS             tag Boots, ou a receita contém, transitivamente, um
                       item com tag Boots
  7. COMPONENT         attributes.into não vazio
  8. BASIC_OR_STARTER  receita vazia
  9. MAIN              qualquer outro caso (tem receita, não é componente)

Para a curadoria sintética `curated_demo_v1`, a flag explícita
`flags.completed` decide: verdadeira com receita -> MAIN; falsa -> COMPONENT;
ausente ou inconsistente -> UNCLASSIFIED. Qualquer outra versão de curadoria
(inclusive o legado `data_dragon_v4`, que não persiste `into`) produz
UNCLASSIFIED para todo item: o consumidor trata como incompatível, nunca
assume principal. Nunca são usados nome, descrição, texto livre nem preço
como regra.

Item ausente do catálogo (UNKNOWN) e item excluído da curadoria (EXCLUDED)
são classes próprias, distintas de "não é principal": o consumidor não pode
inferir nada sobre eles.

Contrato de entrada: `classify_catalog` exige o catálogo COMPLETO ou, no
mínimo, o fechamento transitivo de receita dos itens que se quer classificar.
A regra 6 (botas por receita) e tudo que vem depois dependem da receita
inteira; se algum componente falta no mapa recebido, ou uma entrada de
receita é malformada, e nenhuma prova positiva de botas foi encontrada nos
componentes visíveis, o item é UNCLASSIFIED
(`CLASSIFICATION_INCOMPLETE_RECIPE_CLOSURE`) — nunca MAIN nem BOOTS por
omissão. As classes decididas ANTES da regra 6 (1 a 5, e a tag Boots) não
dependem da receita e não são afetadas.

Limitação conhecida de `item_classification_v1` (ids "gêmeos"): os catálogos
15.1.1, 16.16.1 e 16.17.1 têm itens com id >= 100000 (ex.: 323003, 667666)
que repetem o nome de um item real de id menor, são legais só no mapa 11 e
passam por todas as regras como MAIN. Nenhum campo documentado do Data Dragon
os distingue, e esta versão não aplica override nem regra por nome. Não afeta
a seleção de decisões (parte do item efetivamente comprado; nenhum dos 1798
itens comprados no corpus real analisado tem id >= 100000), mas quem
enumerar itens MAIN como candidatos deve tratar isso na legalidade/candidatos
(CC-301)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeGuard

from riftlab.catalog.domain import Item

ITEM_CLASSIFICATION_VERSION = "item_classification_v1"

_CURATION_DATA_DRAGON_V5 = "data_dragon_v5"
_CURATION_DEMO_V1 = "curated_demo_v1"
_SUMMONERS_RIFT_MAP_ID = 11


class ItemClass(StrEnum):
    NOT_PURCHASABLE = "NOT_PURCHASABLE"
    MAP_ILLEGAL = "MAP_ILLEGAL"
    CHAMPION_SPECIFIC = "CHAMPION_SPECIFIC"
    TRINKET = "TRINKET"
    CONSUMABLE = "CONSUMABLE"
    BOOTS = "BOOTS"
    COMPONENT = "COMPONENT"
    BASIC_OR_STARTER = "BASIC_OR_STARTER"
    MAIN = "MAIN"
    # Sem classificação confiável (curadoria sem suporte, campo ausente ou
    # inconsistente): incompatível, nunca assumido principal.
    UNCLASSIFIED = "UNCLASSIFIED"
    # Presente na fonte, excluído da curadoria com motivo (ex.: specialRecipe).
    EXCLUDED = "EXCLUDED"
    # item_id inexistente neste catálogo.
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ItemClassification:
    item_id: int
    item_class: ItemClass
    rule_code: str
    reason: str


@dataclass(frozen=True, slots=True)
class ItemClassificationTable:
    """Resultado da classificação de um catálogo inteiro, com as versões que
    o produziram. `classify` cobre também ids fora da tabela (UNKNOWN)."""

    classification_version: str
    curation_rules_version: str
    by_item_id: Mapping[int, ItemClassification]

    def classify(self, item_id: int) -> ItemClassification:
        found = self.by_item_id.get(item_id)
        if found is not None:
            return found
        return ItemClassification(
            item_id=item_id,
            item_class=ItemClass.UNKNOWN,
            rule_code="ITEM_NOT_IN_CATALOG",
            reason="item_id inexistente neste catálogo",
        )


def _unclassified(item_id: int, rule_code: str, reason: str) -> ItemClassification:
    return ItemClassification(item_id, ItemClass.UNCLASSIFIED, rule_code, reason)


def _missing_field(item_id: int, field: str) -> ItemClassification:
    return _unclassified(
        item_id, "CLASSIFICATION_MISSING_FIELD", f"campo {field} ausente ou com tipo inválido"
    )


def _is_int_list(value: Any) -> TypeGuard[list[int]]:
    return isinstance(value, list) and all(
        isinstance(entry, int) and not isinstance(entry, bool) for entry in value
    )


def _has_boots_tag(item: Item) -> bool:
    tags = item.attributes.get("tags")
    return isinstance(tags, list) and "Boots" in tags


def _recipe_contains_boots(
    item: Item, items: Mapping[int, Item], visiting: frozenset[int] = frozenset()
) -> bool | None:
    """True se algum componente (transitivo) tem a tag Boots (prova positiva,
    mesmo que outra parte da receita seja desconhecida). False se o fechamento
    de receita é COMPLETO e nenhum componente tem a tag. None se o fechamento
    é incompleto (componente ausente do mapa ou entrada de receita malformada)
    e nenhuma prova positiva foi encontrada: não dá para afirmar nada. Um
    ciclo (impossível num catálogo sincronizado, mas esta função é pura e não
    confia na entrada) termina sem afirmar botas."""
    if item.item_id in visiting:
        return False
    visiting = visiting | {item.item_id}
    incomplete = False
    for component in item.recipe:
        component_id = component.get("itemId") if isinstance(component, dict) else None
        if isinstance(component_id, bool) or not isinstance(component_id, int):
            incomplete = True
            continue
        component_item = items.get(component_id)
        if component_item is None:
            incomplete = True
            continue
        if _has_boots_tag(component_item):
            return True
        nested = _recipe_contains_boots(component_item, items, visiting)
        if nested is True:
            return True
        if nested is None:
            incomplete = True
    return None if incomplete else False


def _classify_data_dragon_v5(item: Item, items: Mapping[int, Item]) -> ItemClassification:
    item_id = item.item_id
    purchasable = item.flags.get("purchasable")
    if not isinstance(purchasable, bool):
        return _missing_field(item_id, "flags.purchasable")
    maps = item.flags.get("maps")
    if not _is_int_list(maps):
        return _missing_field(item_id, "flags.maps")
    tags = item.attributes.get("tags")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        return _missing_field(item_id, "attributes.tags")
    into = item.attributes.get("into")
    if not _is_int_list(into):
        return _missing_field(item_id, "attributes.into")

    def result(item_class: ItemClass, rule_code: str, reason: str) -> ItemClassification:
        return ItemClassification(item_id, item_class, rule_code, reason)

    if not purchasable:
        return result(ItemClass.NOT_PURCHASABLE, "DD5_NOT_PURCHASABLE", "flags.purchasable é false")
    if _SUMMONERS_RIFT_MAP_ID not in maps:
        return result(ItemClass.MAP_ILLEGAL, "DD5_MAP_ILLEGAL", "mapa 11 ausente de flags.maps")
    if item.flags.get("requiredChampion"):
        return result(
            ItemClass.CHAMPION_SPECIFIC,
            "DD5_CHAMPION_SPECIFIC",
            "flags.requiredChampion presente",
        )
    if "Trinket" in tags:
        return result(ItemClass.TRINKET, "DD5_TRINKET_TAG", "tag Trinket")
    if "Consumable" in tags:
        return result(ItemClass.CONSUMABLE, "DD5_CONSUMABLE_TAG", "tag Consumable")
    if item.flags.get("consumed") is True:
        return result(ItemClass.CONSUMABLE, "DD5_CONSUMABLE_FLAG", "flags.consumed verdadeiro")
    if "Boots" in tags:
        return result(ItemClass.BOOTS, "DD5_BOOTS_TAG", "tag Boots")
    boots_by_recipe = _recipe_contains_boots(item, items)
    if boots_by_recipe is None:
        return _unclassified(
            item_id,
            "CLASSIFICATION_INCOMPLETE_RECIPE_CLOSURE",
            "componente de receita ausente do catálogo recebido ou entrada malformada",
        )
    if boots_by_recipe:
        return result(
            ItemClass.BOOTS,
            "DD5_BOOTS_RECIPE",
            "receita contém, transitivamente, um item com tag Boots",
        )
    if into:
        return result(
            ItemClass.COMPONENT,
            "DD5_HAS_INTO",
            "attributes.into não vazio (componente de outro item)",
        )
    if not item.recipe:
        return result(
            ItemClass.BASIC_OR_STARTER, "DD5_NO_RECIPE", "receita vazia (item básico ou inicial)"
        )
    return result(
        ItemClass.MAIN,
        "DD5_MAIN",
        "tem receita, não é componente e nenhuma regra de exclusão se aplica",
    )


def _classify_demo_v1(item: Item) -> ItemClassification:
    completed = item.flags.get("completed")
    if not isinstance(completed, bool):
        return _missing_field(item.item_id, "flags.completed")
    if not completed:
        return ItemClassification(
            item.item_id, ItemClass.COMPONENT, "DEMO1_NOT_COMPLETED", "flags.completed é false"
        )
    if not item.recipe:
        return _unclassified(
            item.item_id,
            "DEMO1_COMPLETED_WITHOUT_RECIPE",
            "flags.completed verdadeiro mas receita vazia (inconsistente)",
        )
    return ItemClassification(
        item.item_id, ItemClass.MAIN, "DEMO1_COMPLETED", "flags.completed verdadeiro com receita"
    )


def classify_catalog(
    items: Mapping[int, Item],
    *,
    curation_rules_version: str,
    excluded_item_ids: frozenset[int] = frozenset(),
) -> ItemClassificationTable:
    """Classifica todos os itens do catálogo. `items` é o catálogo completo
    (a receita de um item é resolvida dentro dele). Ids excluídos têm
    prioridade: um item que aparece nos dois conjuntos (entrada inconsistente)
    é EXCLUDED."""
    by_item_id: dict[int, ItemClassification] = {}
    for item_id in sorted(excluded_item_ids):
        by_item_id[item_id] = ItemClassification(
            item_id,
            ItemClass.EXCLUDED,
            "EXCLUDED_BY_CURATION",
            "item excluído da curadoria do catálogo",
        )
    for item_id in sorted(items):
        if item_id in by_item_id:
            continue
        item = items[item_id]
        if curation_rules_version == _CURATION_DATA_DRAGON_V5:
            by_item_id[item_id] = _classify_data_dragon_v5(item, items)
        elif curation_rules_version == _CURATION_DEMO_V1:
            by_item_id[item_id] = _classify_demo_v1(item)
        else:
            by_item_id[item_id] = _unclassified(
                item_id,
                "CLASSIFICATION_UNSUPPORTED_CURATION",
                f"curadoria {curation_rules_version!r} sem regra de classificação",
            )
    return ItemClassificationTable(
        classification_version=ITEM_CLASSIFICATION_VERSION,
        curation_rules_version=curation_rules_version,
        by_item_id=by_item_id,
    )
