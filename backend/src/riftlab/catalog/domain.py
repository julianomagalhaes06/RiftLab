from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import SourceSchemaError

CURRENT_ITEM_SCHEMA_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class CatalogVersion:
    """Catálogo imutável de um patch. Uma nova versão nunca sobrescreve uma
    existente; correções viram uma nova linha, conforme domain-model.md.

    sha256 (CC-202) é o hash de IDENTIDADE de conteúdo do catálogo — cobre o
    raw fetched da fonte, mapping_version e a versão das regras de curadoria
    aplicadas (nunca só os bytes brutos): uma mudança em qualquer um dos três
    produz uma identidade nova, nunca reaproveita conteúdo derivado
    desatualizado (pedido explícito do plano do CC-202). Único no banco
    (índice único parcial em models.py) — a mesma identidade de conteúdo
    nunca duplica linha (sync idempotente); conteúdo divergente sempre vira
    uma linha nova, nunca sobrescreve a existente."""

    id: uuid.UUID
    game_patch: str
    data_dragon_version: str
    sha256: str
    mapping_version: str


@dataclass(frozen=True, slots=True)
class Item:
    """Item só tem sentido dentro do catálogo (catalog_id, item_id). O mesmo
    item_id pode existir em catálogos distintos com dados diferentes."""

    catalog_id: uuid.UUID
    item_id: int
    price: int
    recipe: list[dict[str, Any]] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CURRENT_ITEM_SCHEMA_VERSION


class CatalogRepository(Protocol):
    async def add_catalog_version(self, catalog: CatalogVersion) -> None:
        """Levanta ConflictError se já existe uma linha com o mesmo sha256
        (identidade de conteúdo) — sync idempotente (CC-202): o chamador
        perdedor de uma corrida real nunca duplica, reconsulta e reaproveita
        a linha vencedora (mesmo padrão de ingestion.create_import_job)."""
        ...

    async def add_item(self, item: Item) -> None: ...

    async def get_catalog_version(self, catalog_id: uuid.UUID) -> CatalogVersion | None: ...

    async def get_item(self, catalog_id: uuid.UUID, item_id: int) -> Item | None: ...

    async def find_catalog_version_by_content_hash(self, sha256: str) -> CatalogVersion | None:
        """Reconsulta após ConflictError de add_catalog_version, ou checagem
        de idempotência antes de tentar persistir de novo."""
        ...

    async def find_latest_catalog_version(
        self, game_patch: str, data_dragon_version: str, mapping_version: str
    ) -> CatalogVersion | None:
        """Resolução determinística partida->catálogo (CC-202): dado o
        mapping já resolvido (game_patch, data_dragon_version, mapping_version
        — os três fixados por configuração explícita, nunca "latest"
        implícito), devolve o catálogo mais recente sincronizado para essa
        identidade exata, ou None (MISSING_CATALOG — nunca sincronizado).
        Mais de uma linha para a mesma identidade só ocorre se o conteúdo
        upstream divergiu entre sincronizações (nunca sobrescrito); a mais
        recente é a canônica para novas resoluções — catálogos antigos
        continuam consultáveis por ID exato, nunca reescritos."""
        ...


@dataclass(frozen=True, slots=True)
class CatalogItemSourceDTO:
    """Item como a fonte bruta descreve, anterior à persistência como Item (que
    exige catalog_id já atribuído). recipe é uma lista de
    {"itemId": int, "quantity": int} — multiconjunto explícito de
    componentes, nunca um conjunto: dois componentes iguais na receita real
    (ex.: dois Adagas) viram uma entrada com quantity=2, nunca colapsados
    silenciosamente em quantity=1 (CC-202, domain-model.md: "inventário é
    multiconjunto, não conjunto")."""

    item_id: int
    price: int
    recipe: list[dict[str, Any]] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CURRENT_ITEM_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class CatalogSourceResult:
    """Retorno bruto de CatalogSource, anterior a qualquer persistência como
    CatalogVersion/Item (CC-105/CC-202). origin=CURATED_DEMO é fixture
    sintética; origin=COMPLETED_MATCH (CC-202) é Data Dragon real, mesmo
    reaproveitamento do enum de MatchSource (CC-104) — sem introduzir um
    terceiro valor só para catálogo. curation_rules_version (CC-202)
    identifica a versão das regras de normalização/curadoria aplicadas ao
    raw (ex.: como recipe é extraído de "from", quais itens especiais são
    excluídos) — parte da identidade de conteúdo do catálogo persistido
    (CatalogVersion.sha256), para uma mudança de curadoria nunca reaproveitar
    conteúdo derivado desatualizado."""

    game_patch: str
    origin: SourceOrigin
    schema_version: str
    raw_hash: str
    data_dragon_version: str
    mapping_version: str
    curation_rules_version: str
    items: tuple[CatalogItemSourceDTO, ...]
    excluded_items: tuple[ExcludedCatalogItem, ...] = ()


@dataclass(frozen=True, slots=True)
class ExcludedCatalogItem:
    """Item presente na fonte bruta mas excluído da curadoria com motivo
    explícito (CC-202, plano: "excluir/qualificar com motivo explícito
    quando pertinente" — nunca descartado silenciosamente, nunca inventa
    regra para incluir à força uma classe de item ainda não suportada)."""

    item_id: int
    reason_code: str
    detail: str


class RecipeCycleError(SourceSchemaError):
    """Receita com ciclo direto ou indireto entre itens do mesmo catálogo —
    permanente, nunca aceita parcialmente (CC-202)."""


class RecipeReferenceError(SourceSchemaError):
    """Receita referencia um item_id que não existe no mesmo catálogo —
    permanente, nunca aceita parcialmente (CC-202)."""


def validate_item_types_and_limits(items: tuple[CatalogItemSourceDTO, ...]) -> None:
    """Defesa em profundidade comum a qualquer CatalogSource (CC-202): nunca
    deixa um tipo/limite inválido escapar até uma constraint crua do banco
    (mesma lição do parser Riot, CC-201) — item_id/quantity/price precisam
    ser inteiros não-negativos de verdade (bool nunca conta como int)."""
    seen_ids: set[int] = set()
    for item in items:
        if isinstance(item.item_id, bool) or not isinstance(item.item_id, int) or item.item_id < 0:
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=f"item_id inválido: {item.item_id!r}",
            )
        if item.item_id in seen_ids:
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=f"item_id duplicado no catálogo: {item.item_id}",
            )
        seen_ids.add(item.item_id)
        if isinstance(item.price, bool) or not isinstance(item.price, int) or item.price < 0:
            raise SourceSchemaError(
                code="CATALOG_SOURCE_MISSING_FIELD",
                detail=f"item {item.item_id} com price inválido: {item.price!r}",
            )
        for component in item.recipe:
            component_id = component.get("itemId")
            quantity = component.get("quantity")
            if isinstance(component_id, bool) or not isinstance(component_id, int):
                raise SourceSchemaError(
                    code="CATALOG_SOURCE_MISSING_FIELD",
                    detail=(
                        f"item {item.item_id} com itemId de componente inválido: {component_id!r}"
                    ),
                )
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                raise SourceSchemaError(
                    code="CATALOG_SOURCE_MISSING_FIELD",
                    detail=f"item {item.item_id} com quantity de componente inválida: {quantity!r}",
                )


def validate_recipe_graph(items: tuple[CatalogItemSourceDTO, ...]) -> None:
    """Validação pura de grafo de receitas, comum a qualquer CatalogSource
    (demo ou real, CC-202): toda referência de componente existe no mesmo
    catálogo, sem ciclo direto (item referencia a si mesmo) nem indireto
    (A -> B -> A), sem exigir reconciliação de preço aqui (isso é conferido
    separadamente por quem monta o preço). Nunca aceita catálogo parcial:
    a primeira violação encontrada aborta a validação inteira."""
    by_id = {item.item_id: item for item in items}
    for item in items:
        for component in item.recipe:
            component_id = component.get("itemId")
            if component_id not in by_id:
                raise RecipeReferenceError(
                    code="CATALOG_RECIPE_MISSING_REFERENCE",
                    detail=(
                        f"item {item.item_id} referencia componente "
                        f"{component_id!r} inexistente no catálogo"
                    ),
                )

    visiting: set[int] = set()
    resolved: set[int] = set()

    def _visit(item_id: int, path: list[int]) -> None:
        if item_id in resolved:
            return
        if item_id in visiting:
            cycle = " -> ".join(str(p) for p in (*path, item_id))
            raise RecipeCycleError(
                code="CATALOG_RECIPE_CYCLE", detail=f"ciclo de receita detectado: {cycle}"
            )
        visiting.add(item_id)
        for component in by_id[item_id].recipe:
            _visit(component["itemId"], [*path, item_id])
        visiting.discard(item_id)
        resolved.add(item_id)

    for item in items:
        _visit(item.item_id, [])


class CatalogSource(Protocol):
    """Fonte plugável de catálogo. Consumidor depende só do Protocol; nenhuma URL
    ou caminho arbitrário vindo de usuário chega até uma implementação real."""

    async def load_catalog(self, game_patch: str) -> CatalogSourceResult:
        """Levanta SourceNotFoundError para game_patch fora do escopo da fonte e
        SourceSchemaError para JSON inválido, schema desconhecido ou item/campo
        estrutural obrigatório ausente."""
        ...
