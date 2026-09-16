"""Reconstrução pré-compra de inventário (CC-203): reduz eventos em ordem
(timestamp_ms, source_index) e guarda o estado ANTES de cada compra elegível.
O reducer (reconstruct_participant_purchases) é puro — sem I/O, sem relógio,
sem aleatoriedade — para que "perturbar eventos/frames após um corte mantém
o snapshot anterior idêntico" seja garantido por construção, não só testado.
reconstruct_match é o orquestrador (I/O): busca fora de transação -> reduz
(puro) -> persiste numa transação única, mesmo padrão de
catalog.application.sync_catalog (CC-202)."""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.domain import Item
from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping
from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.ingestion.domain import MatchSource
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.api.enums import BudgetSource
from riftlab.shared.db import unit_of_work
from riftlab.shared.errors import ConflictError, NotFoundError
from riftlab.timeline.domain import (
    CURRENT_DECISION_STATE_SCHEMA_VERSION,
    DecisionState,
    TimelineEvent,
    TimelineReplayError,
)
from riftlab.timeline.repository import SqlAlchemyTimelineRepository

SessionFactory = async_sessionmaker[AsyncSession]

STALE_FRAME_THRESHOLD_MS = 90_000

# 6 slots normais (docs/domain-model.md) — trinket é uma slot própria, nunca
# conta aqui. Achado real da revisão: um inventário com mais de 6 itens
# normais distintos/empilhados além da regra do catálogo passava sem alerta.
_MAX_NORMAL_SLOTS = 6

# Tipos de evento que afetam o inventário de item (CC-105: os únicos 4 tipos
# reais de mutação de item observados na amostra auditada). Qualquer outro
# type (WARD_PLACED, CHAMPION_KILL, LEVEL_UP...) é ignorado para fins de
# inventário sem validação estrutural — nunca deduzimos posse a partir deles.
_RELEVANT_EVENT_TYPES = frozenset({"ITEM_PURCHASED", "ITEM_SOLD", "ITEM_DESTROYED", "ITEM_UNDO"})


class FrameLike(Protocol):
    """Estrutural (duck typing) para não acoplar timeline a ingestion.domain:
    TimelineFrameDTO (CC-104/201) satisfaz isto sem import cruzado. Exposto
    como property (não atributo simples) para o Protocol aceitar tipos
    covariantes compatíveis (ex.: dict[int, dict[str, Any]]) sem exigir
    igualdade exata de tipo."""

    @property
    def timestamp_ms(self) -> int: ...

    @property
    def participant_frames(self) -> Mapping[int, Mapping[str, Any]]: ...


def _sorted_items(counts: dict[int, int]) -> tuple[tuple[int, int], ...]:
    return tuple(sorted((item_id, count) for item_id, count in counts.items() if count > 0))


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    """Multiconjunto imutável (contagem sempre > 0 nas entradas presentes).
    trinket_item_id é uma slot separada: nunca faz parte de items, nunca
    consumida por receita — achado real (consulta ao catálogo sincronizado
    nesta sessão contra o Data Dragon 16.17.1 de verdade): todo item com a
    tag "Trinket" tem recipe=[] no catálogo real, então trocar de trinket
    nunca aciona consumo de componente, só substitui a slot diretamente."""

    items: tuple[tuple[int, int], ...] = ()
    trinket_item_id: int | None = None

    def owned(self, item_id: int) -> int:
        for existing_id, count in self.items:
            if existing_id == item_id:
                return count
        return 0

    def with_added(self, item_id: int, qty: int = 1) -> InventorySnapshot:
        counts = dict(self.items)
        counts[item_id] = counts.get(item_id, 0) + qty
        return InventorySnapshot(items=_sorted_items(counts), trinket_item_id=self.trinket_item_id)

    def with_removed(self, item_id: int, qty: int = 1) -> InventorySnapshot:
        counts = dict(self.items)
        current = counts.get(item_id, 0)
        remaining = current - qty
        if remaining < 0:
            raise ValueError(
                f"remoção de {qty} unidade(s) de {item_id} excede posse atual ({current})"
            )
        if remaining == 0:
            counts.pop(item_id, None)
        else:
            counts[item_id] = remaining
        return InventorySnapshot(items=_sorted_items(counts), trinket_item_id=self.trinket_item_id)

    def with_trinket(self, item_id: int | None) -> InventorySnapshot:
        return InventorySnapshot(items=self.items, trinket_item_id=item_id)

    def to_payload(self) -> dict[str, Any]:
        return {
            "items": {str(item_id): count for item_id, count in self.items},
            "trinketItemId": self.trinket_item_id,
        }


EMPTY_INVENTORY = InventorySnapshot()


@dataclass(frozen=True, slots=True)
class UncertainReason:
    """Causa segura e datada de incerteza (nunca um booleano solto): sempre
    ancorada no source_index/timestamp do evento que a provocou."""

    code: str
    event_source_index: int
    timestamp_ms: int
    item_id: int | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "eventSourceIndex": self.event_source_index,
            "timestampMs": self.timestamp_ms,
        }
        if self.item_id is not None:
            payload["itemId"] = self.item_id
        return payload


@dataclass(frozen=True, slots=True)
class GoldEstimate:
    """FRAME_ESTIMATE (docs/domain-model.md): nunca ouro exato. origin=UNKNOWN
    cobre tanto "nenhum frame anterior" quanto "frame anterior sem dado do
    participante" — os dois casos são igualmente ausência de observação, não
    um zero disfarçado."""

    value: int | None
    age_ms: int | None
    stale: bool
    origin: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "ageMs": self.age_ms,
            "stale": self.stale,
            "origin": self.origin,
        }


_UNKNOWN_GOLD_ESTIMATE_NO_AGE = GoldEstimate(
    value=None, age_ms=None, stale=False, origin=BudgetSource.UNKNOWN.value
)


def select_gold_estimate(
    frames: Sequence[FrameLike], participant_id: int, event_timestamp_ms: int
) -> GoldEstimate:
    """Último frame ESTRITAMENTE anterior ao evento — nunca mesmo timestamp
    nem futuro por conveniência, nunca interpolado com o frame seguinte.
    origin usa o vocabulário público de budget_source (docs/domain-model.md,
    shared/api/enums.py:BudgetSource) — nunca um rótulo próprio inventado
    (achado da revisão: "OBSERVED" não é um valor do contrato)."""
    candidate: FrameLike | None = None
    for frame in frames:
        if frame.timestamp_ms < event_timestamp_ms and (
            candidate is None or frame.timestamp_ms > candidate.timestamp_ms
        ):
            candidate = frame
    if candidate is None:
        return _UNKNOWN_GOLD_ESTIMATE_NO_AGE

    age_ms = event_timestamp_ms - candidate.timestamp_ms
    stale = age_ms > STALE_FRAME_THRESHOLD_MS
    participant_frame = candidate.participant_frames.get(participant_id)
    gold = participant_frame.get("currentGold") if participant_frame else None
    # bool é subclasse de int; ouro negativo é impossível de verdade (achado
    # da revisão: currentGold=-5 era aceito como observado) — os dois viram
    # UNKNOWN, nunca um valor inválido apresentado como se fosse confiável.
    if gold is None or isinstance(gold, bool) or not isinstance(gold, int) or gold < 0:
        return GoldEstimate(
            value=None, age_ms=age_ms, stale=stale, origin=BudgetSource.UNKNOWN.value
        )
    return GoldEstimate(
        value=gold, age_ms=age_ms, stale=stale, origin=BudgetSource.FRAME_ESTIMATE.value
    )


@dataclass(frozen=True, slots=True)
class ReconstructedPurchase:
    """Uma compra elegível: snapshot é o estado ANTES de aplicar o alvo (o
    item comprado nunca aparece no próprio snapshot). Saída do reducer puro;
    a persistência (DecisionState) é responsabilidade do orquestrador."""

    event_source_index: int
    participant_id: int
    inventory_before: InventorySnapshot
    purchased_item_id: int
    gold_estimate: GoldEstimate
    uncertain: bool
    reasons: tuple[UncertainReason, ...]
    destroy_reconciliation_heuristic_used: bool


@dataclass(frozen=True, slots=True)
class _ReversibleOp:
    """Ledger da última OPERAÇÃO reversível do participante (compra OU
    venda — revisão: venda sempre apagava o ledger, então undo de venda
    nunca tinha caminho suportado, contradizendo o plano). Guarda o
    multiconjunto EXATO anterior à operação, não uma reconstrução
    aproximada a partir de beforeId/afterId — undo restaura esse valor
    diretamente quando a correlação é única, nunca "repara" a partir do
    inventário final. consumed_components (vazio para venda/item sem
    receita) é o conjunto de invariantes que afterId precisa respeitar
    (revisão: comparar só beforeId não comprova correlação — beforeId=999
    era aceito mesmo sem nenhum componente 999 envolvido)."""

    resulting_item_id: int
    prior_inventory: InventorySnapshot
    consumed_components: tuple[tuple[int, int], ...] = ()
    timestamp_ms: int = 0


def _require_int_field(event: TimelineEvent, field_name: str) -> int:
    value = event.payload.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TimelineReplayError(
            code="TIMELINE_EVENT_MALFORMED",
            detail=(
                f"{event.type} em match {event.match_id!r} source_index="
                f"{event.source_index} sem {field_name!r} inteiro válido"
            ),
        )
    return value


def _consume_recipe(
    inventory: InventorySnapshot,
    item: Item,
    catalog_items: Mapping[int, Item],
    consumed_log: list[tuple[int, int]],
) -> InventorySnapshot:
    """Consome a receita de `item` recursivamente (revisão, achado 2): prefere
    sempre um componente já PRONTO possuído diretamente; só decompõe um
    componente faltante em SEUS PRÓPRIOS subcomponentes quando ele tem
    receita conhecida no catálogo (fechamento transitivo carregado pelo
    orquestrador) — nunca consome o mesmo ancestral duas vezes, pois cada
    remoção já muta `inventory` antes do próximo componente ser avaliado.
    Sem receita conhecida para decompor, o restante é só pago em ouro (não
    modelado aqui), igual à compra direta de um item base."""
    for component in item.recipe:
        component_id = int(component["itemId"])
        needed = int(component["quantity"])
        have = inventory.owned(component_id)
        direct_take = min(have, needed)
        if direct_take > 0:
            inventory = inventory.with_removed(component_id, direct_take)
            consumed_log.append((component_id, direct_take))
            needed -= direct_take
        if needed > 0:
            sub_item = catalog_items.get(component_id)
            if sub_item is not None and sub_item.recipe:
                for _ in range(needed):
                    inventory = _consume_recipe(inventory, sub_item, catalog_items, consumed_log)
    return inventory


def _stack_limit(item: Item | None) -> int | None:
    """None = regra de stack desconhecida (item consumível sem `stacks`
    declarado no catálogo) — nunca inventada. 1 = não empilha (regra padrão
    confiável para item não-consumível/desconhecido — a esmagadora maioria
    dos itens reais). >1 = limite real declarado pelo catálogo (achado do
    CC-202: curadoria só grava `flags.stacks` para itens consumíveis)."""
    if item is None or not item.flags.get("consumed"):
        return 1
    stacks = item.flags.get("stacks")
    if isinstance(stacks, int) and not isinstance(stacks, bool) and stacks > 0:
        return stacks
    return None


def _slots_used(
    inventory: InventorySnapshot, catalog_items: Mapping[int, Item]
) -> tuple[int, bool]:
    """Slots normais ocupados (trinket nunca entra, é slot própria) e se
    algum item tinha regra de stack desconhecida (capacidade então não é
    verificável com confiança para aquele item, nunca inventada)."""
    slots = 0
    unknown = False
    for item_id, count in inventory.items:
        limit = _stack_limit(catalog_items.get(item_id))
        if limit is None:
            unknown = True
            continue
        slots += math.ceil(count / limit)
    return slots, unknown


def reconstruct_participant_purchases(
    *,
    participant_id: int,
    events: Sequence[TimelineEvent],
    frames: Sequence[FrameLike] = (),
    catalog_id: uuid.UUID | None,
    catalog_items: Mapping[int, Item] | None = None,
    excluded_item_ids: frozenset[int] = frozenset(),
) -> list[ReconstructedPurchase]:
    """Reducer puro. events deve conter os eventos de TODOS os participantes
    da partida — a filtragem por dono acontece aqui (nunca antes de chamar),
    para que dois participantes processados separadamente fiquem isolados
    por construção, sem se misturar. catalog_id=None é modo degradado
    explícito (sem catálogo resolvido para o patch da partida): toda compra
    vira INVENTORY_UNCERTAIN (NO_CATALOG), mas o replay continua
    melhor-esforço por item_id literal — sem catálogo não há receita
    conhecida para consumir, então nenhum componente é removido."""
    catalog_items = catalog_items if catalog_items is not None else {}
    catalog_available = catalog_id is not None

    owned_events = sorted(
        (
            event
            for event in events
            if event.type in _RELEVANT_EVENT_TYPES
            and event.payload.get("participantId") == participant_id
        ),
        key=lambda event: (event.timestamp_ms, event.source_index),
    )

    inventory = EMPTY_INVENTORY
    uncertain = False
    reasons: list[UncertainReason] = []
    destroy_heuristic_used = False
    last_reversible: _ReversibleOp | None = None
    # pending_destroys[timestamp_ms][component_item_id] = unidades consumidas
    # por uma compra de receita neste mesmo instante, ainda não confirmadas
    # por um ITEM_DESTROYED real — nunca remove a mesma unidade duas vezes.
    pending_destroys: dict[int, dict[int, int]] = {}
    results: list[ReconstructedPurchase] = []

    def _mark_uncertain(code: str, event: TimelineEvent, item_id: int | None = None) -> None:
        nonlocal uncertain
        uncertain = True
        reasons.append(
            UncertainReason(
                code=code,
                event_source_index=event.source_index,
                timestamp_ms=event.timestamp_ms,
                item_id=item_id,
            )
        )

    def _discard_pending(consumed: tuple[tuple[int, int], ...], timestamp_ms: int) -> None:
        """Desfaz a expectativa de destroy de uma compra desfeita por undo
        (revisão, achado 1): sem isto, um ITEM_DESTROYED real chegando depois
        no MESMO timestamp (source_index maior, ordem de evento preservada)
        podia se reconciliar contra a compra já desfeita — never removido de
        verdade do inventário, mas contabilizado como se tivesse sido."""
        if not consumed:
            return
        bucket = pending_destroys.get(timestamp_ms)
        if not bucket:
            return
        for component_id, qty in consumed:
            if component_id in bucket:
                bucket[component_id] = max(0, bucket[component_id] - qty)

    for event in owned_events:
        if event.type == "ITEM_PURCHASED":
            item_id = _require_int_field(event, "itemId")
            before = inventory
            consumed: list[tuple[int, int]] = []

            if not catalog_available:
                _mark_uncertain("NO_CATALOG", event, item_id)
                inventory = inventory.with_added(item_id, 1)
            elif item_id in catalog_items:
                item = catalog_items[item_id]
                new_inventory = _consume_recipe(inventory, item, catalog_items, consumed)
                is_trinket = "Trinket" in item.attributes.get("tags", ())
                new_inventory = (
                    new_inventory.with_trinket(item_id)
                    if is_trinket
                    else new_inventory.with_added(item_id, 1)
                )
                inventory = new_inventory
                if consumed:
                    bucket = pending_destroys.setdefault(event.timestamp_ms, {})
                    for component_id, take in consumed:
                        bucket[component_id] = bucket.get(component_id, 0) + take
            elif item_id in excluded_item_ids:
                _mark_uncertain("ITEM_EXCLUDED_UNSUPPORTED", event, item_id)
                inventory = inventory.with_added(item_id, 1)
            else:
                _mark_uncertain("ITEM_UNKNOWN", event, item_id)
                inventory = inventory.with_added(item_id, 1)

            # Capacidade verificada sempre (não só no caminho de catálogo
            # conhecido): um item desconhecido/excluído/sem catálogo também
            # ocupa uma slot normal (trinket nunca entra em `.items`, então
            # esta checagem é naturalmente um no-op para ele).
            slots, stack_unknown = _slots_used(inventory, catalog_items)
            if stack_unknown:
                _mark_uncertain("ITEM_STACK_RULE_UNKNOWN", event, item_id)
            elif slots > _MAX_NORMAL_SLOTS:
                _mark_uncertain("INVENTORY_CAPACITY_EXCEEDED", event, item_id)

            results.append(
                ReconstructedPurchase(
                    event_source_index=event.source_index,
                    participant_id=participant_id,
                    inventory_before=before,
                    purchased_item_id=item_id,
                    gold_estimate=select_gold_estimate(frames, participant_id, event.timestamp_ms),
                    uncertain=uncertain,
                    reasons=tuple(reasons),
                    destroy_reconciliation_heuristic_used=destroy_heuristic_used,
                )
            )
            last_reversible = _ReversibleOp(
                resulting_item_id=item_id,
                prior_inventory=before,
                consumed_components=tuple(consumed),
                timestamp_ms=event.timestamp_ms,
            )

        elif event.type == "ITEM_SOLD":
            item_id = _require_int_field(event, "itemId")
            before = inventory
            if inventory.trinket_item_id == item_id:
                inventory = inventory.with_trinket(None)
                last_reversible = _ReversibleOp(
                    resulting_item_id=item_id,
                    prior_inventory=before,
                    timestamp_ms=event.timestamp_ms,
                )
            elif inventory.owned(item_id) > 0:
                inventory = inventory.with_removed(item_id, 1)
                # venda também é reversível (revisão: "venda sempre apaga o
                # ledger" deixava undo de venda sem caminho suportado, contra
                # o plano) — resulting_item_id é o item vendido (o mesmo que
                # beforeId de um ITEM_UNDO correlacionado precisa bater).
                last_reversible = _ReversibleOp(
                    resulting_item_id=item_id,
                    prior_inventory=before,
                    timestamp_ms=event.timestamp_ms,
                )
            else:
                _mark_uncertain("SALE_WITHOUT_OWNERSHIP", event, item_id)

        elif event.type == "ITEM_DESTROYED":
            item_id = _require_int_field(event, "itemId")
            pending = pending_destroys.get(event.timestamp_ms)
            if pending and pending.get(item_id, 0) > 0:
                # Reconciliado por heurística de timestamp/item/quantidade
                # (revisão, achado 3): NUNCA removido de novo (evita dupla
                # contagem), mas isto é uma ESTIMATIVA, nunca comprovada
                # contra o corpus real (CC-105 ausente nesta sessão) —
                # propagado explicitamente em quality, nunca junto de
                # `uncertain` (que bloqueia recomendação; aqui não bloqueia,
                # só desconta o crédito de certeza empírica).
                pending[item_id] -= 1
                destroy_heuristic_used = True
            else:
                _mark_uncertain("UNRECONCILED_ITEM_DESTROYED", event, item_id)
                if inventory.trinket_item_id == item_id:
                    inventory = inventory.with_trinket(None)
                elif inventory.owned(item_id) > 0:
                    inventory = inventory.with_removed(item_id, 1)
                else:
                    _mark_uncertain("DESTROY_WITHOUT_OWNERSHIP", event, item_id)
                last_reversible = None

        elif event.type == "ITEM_UNDO":
            before_id = _require_int_field(event, "beforeId")
            after_id = _require_int_field(event, "afterId")
            valid_after_ids = {0}
            if last_reversible is not None:
                valid_after_ids |= {
                    component_id for component_id, _ in last_reversible.consumed_components
                }
            if (
                last_reversible is None
                or last_reversible.resulting_item_id != before_id
                or after_id not in valid_after_ids
            ):
                # Revisão, achado 1: comparar só beforeId não comprova
                # correlação — beforeId batendo com afterId contraditório
                # (ex.: item nunca consumido por esta operação) nunca é
                # aplicado às cegas, mesmo com beforeId correto.
                _mark_uncertain("UNDO_UNCORRELATED", event, before_id)
            else:
                inventory = last_reversible.prior_inventory
                _discard_pending(last_reversible.consumed_components, last_reversible.timestamp_ms)
                last_reversible = None

    return results


def _decision_state_from_purchase(
    match_id: str, catalog_id: uuid.UUID | None, purchase: ReconstructedPurchase
) -> DecisionState:
    return DecisionState(
        id=uuid.uuid4(),
        match_id=match_id,
        participant_id=purchase.participant_id,
        event_source_index=purchase.event_source_index,
        state_version=1,
        snapshot={
            "inventory": purchase.inventory_before.to_payload(),
            "purchasedItemId": purchase.purchased_item_id,
        },
        features={},  # seleção/extração de features é CC-204, não este card
        quality={
            "uncertain": purchase.uncertain,
            "reasons": [reason.to_payload() for reason in purchase.reasons],
            "goldEstimate": purchase.gold_estimate.to_payload(),
            # Revisão, achado 3: reconciliação de ITEM_DESTROYED por
            # timestamp/item/quantidade é uma HEURÍSTICA nunca validada
            # contra o corpus real (data/raw/cc-105/ ausente nesta sessão,
            # BLOCKED — .private/validation/sprint-2/CC-203/01-corpus-
            # investigation-blocked.txt). Propagado sempre, mesmo quando não
            # aplicado (validated=False é uma propriedade do MÉTODO, não do
            # resultado desta compra específica) — nunca confundido com
            # `uncertain` (que bloqueia recomendação; isto só desconta
            # certeza empírica de uma estimativa que continua sendo o
            # melhor esforço disponível).
            "destroyReconciliationHeuristic": {
                "appliedToThisParticipant": purchase.destroy_reconciliation_heuristic_used,
                "validatedAgainstRealCorpus": False,
                "note": (
                    "correlação por timestamp_ms/item_id/quantidade entre compra de "
                    "receita e ITEM_DESTROYED; nunca confirmada contra dado real "
                    "(corpus CC-105 indisponível nesta sessão) — estimativa, não certeza"
                ),
            },
        },
        schema_version=CURRENT_DECISION_STATE_SCHEMA_VERSION,
        catalog_id=catalog_id,
    )


async def _load_catalog_items_closure(
    catalog_repo: SqlAlchemyCatalogRepository, catalog_id: uuid.UUID, seed_item_ids: set[int]
) -> dict[int, Item]:
    """Carrega o FECHAMENTO TRANSITIVO de receita a partir dos item_ids
    literalmente comprados/vendidos/destruídos (revisão, achado 2): um item
    intermediário nunca comprado diretamente (ex.: B, quando só A e C=f(B)
    aparecem nos eventos) precisa estar disponível para `_consume_recipe`
    decompor C até A quando B não está na posse — sem isto, o orquestrador
    só via os IDs presentes em eventos, nunca as receitas intermediárias."""
    resolved: dict[int, Item] = {}
    pending = set(seed_item_ids)
    while pending:
        item_id = pending.pop()
        if item_id in resolved:
            continue
        item = await catalog_repo.get_item(catalog_id, item_id)
        if item is None:
            continue
        resolved[item_id] = item
        for component in item.recipe:
            component_id = component.get("itemId")
            if (
                isinstance(component_id, int)
                and not isinstance(component_id, bool)
                and component_id not in resolved
            ):
                pending.add(component_id)
    return resolved


async def _referenced_item_ids(events: Sequence[TimelineEvent]) -> set[int]:
    ids: set[int] = set()
    for event in events:
        if event.type not in _RELEVANT_EVENT_TYPES:
            continue
        for field_name in ("itemId", "beforeId", "afterId"):
            value = event.payload.get(field_name)
            if isinstance(value, int) and not isinstance(value, bool) and value != 0:
                ids.add(value)
    return ids


@dataclass(frozen=True, slots=True)
class MatchReconstruction:
    """Resultado do orquestrador: um catalog_id fixo (ou None) e os estados
    de TODOS os participantes, prontos para o CC-204 consumir por
    (match_id, participant_id)."""

    match_id: str
    catalog_id: uuid.UUID | None
    decision_states: list[DecisionState] = field(default_factory=list)


async def reconstruct_match(
    session_factory: SessionFactory,
    mapping: CatalogMapping,
    match_source: MatchSource,
    match_id: str,
) -> MatchReconstruction:
    """Orquestrador (CC-203): busca (banco+fonte local, fora de transação de
    escrita) -> reduz (puro, um participante por vez) -> persiste (uma única
    transação, todos os participantes juntos ou nada). Idempotente via marca
    explícita `match_reconstructions` (revisão, achado 5: checar só
    `list_decision_states` do primeiro participante falhava quando esse
    participante especificamente não tinha compra alguma). Catálogo é
    resolvido e fixado uma única vez aqui; a reexecução idempotente devolve o
    catalog_id REALMENTE usado (da marca persistida), nunca um valor
    reresolvido na hora — mesmo que uma sincronização concorrente já tenha
    trocado o canônico atual (catalog_id obtido continua consultável por ID
    exato e imutável, docs/domain-model.md)."""
    ingestion_repo_factory = SqlAlchemyIngestionRepository
    timeline_repo_factory = SqlAlchemyTimelineRepository
    catalog_repo_factory = SqlAlchemyCatalogRepository

    async with unit_of_work(session_factory) as session:
        match = await ingestion_repo_factory(session).get_match(match_id)
    if match is None:
        raise NotFoundError(f"match {match_id!r} não encontrado; importar antes de reconstruir")

    # Fonte local (fixture demo ou raw imutável real já persistido pelo
    # CC-201): nunca coleta nova, só re-leitura — LocalRawMatchSource é
    # estruturalmente incapaz de emitir rede (revisão, achado 4). Frames não
    # são persistidos pelo CC-201 (só eventos), então esta é a única forma
    # de obtê-los aqui; raw_hash é conferido contra o já persistido — nunca
    # mistura uma releitura divergente (fonte trocada/corrompida) com os
    # eventos já persistidos da mesma partida.
    source_result = await match_source.load_match(match_id)
    if source_result.raw_hash != match.raw_hash:
        raise TimelineReplayError(
            code="SOURCE_RAW_HASH_MISMATCH",
            detail=(
                f"raw recarregado para {match_id!r} diverge do raw_hash já persistido "
                f"({source_result.raw_hash} != {match.raw_hash}); reconstrução nunca "
                "mistura fonte divergente com eventos já persistidos"
            ),
        )
    participant_ids = [p.participant_id for p in source_result.participants]

    async with unit_of_work(session_factory) as session:
        marker = await timeline_repo_factory(session).get_match_reconstruction(match_id)
    if marker is not None:
        all_existing: list[DecisionState] = []
        async with unit_of_work(session_factory) as session:
            repo = timeline_repo_factory(session)
            for participant_id in participant_ids:
                all_existing.extend(await repo.list_decision_states(match_id, participant_id))
        return MatchReconstruction(
            match_id=match_id, catalog_id=marker.catalog_id, decision_states=all_existing
        )

    catalog_id: uuid.UUID | None = None
    entry = mapping.resolve(match.patch)
    if entry is not None:
        async with unit_of_work(session_factory) as session:
            catalog_version = await catalog_repo_factory(session).find_canonical_catalog_version(
                match.patch, entry.data_dragon_version, mapping.mapping_version
            )
        catalog_id = catalog_version.id if catalog_version is not None else None

    async with unit_of_work(session_factory) as session:
        events = await timeline_repo_factory(session).list_events(match_id)

    catalog_items: dict[int, Item] = {}
    excluded_item_ids: frozenset[int] = frozenset()
    if catalog_id is not None:
        seeds = await _referenced_item_ids(events)
        async with unit_of_work(session_factory) as session:
            catalog_repo = catalog_repo_factory(session)
            catalog_items = await _load_catalog_items_closure(catalog_repo, catalog_id, seeds)
            excluded_item_ids = frozenset(
                excluded.item_id for excluded in await catalog_repo.list_excluded_items(catalog_id)
            )

    all_decision_states: list[DecisionState] = []
    for participant_id in participant_ids:
        purchases = reconstruct_participant_purchases(
            participant_id=participant_id,
            events=events,
            frames=source_result.frames,
            catalog_id=catalog_id,
            catalog_items=catalog_items,
            excluded_item_ids=excluded_item_ids,
        )
        all_decision_states.extend(
            _decision_state_from_purchase(match_id, catalog_id, purchase) for purchase in purchases
        )

    try:
        async with unit_of_work(session_factory) as session:
            repo = timeline_repo_factory(session)
            for state in all_decision_states:
                await repo.add_decision_state(state)
            await repo.add_match_reconstruction(match_id, catalog_id)
    except IntegrityError as exc:
        raise ConflictError(
            f"reconstrução concorrente de {match_id!r} já em andamento/concluída"
        ) from exc

    return MatchReconstruction(
        match_id=match_id, catalog_id=catalog_id, decision_states=all_decision_states
    )
