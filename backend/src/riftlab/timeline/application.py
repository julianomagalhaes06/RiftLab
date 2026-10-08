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
from dataclasses import dataclass, field, replace
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
from riftlab.shared.domain import SourceOrigin
from riftlab.shared.errors import ConflictError, NotFoundError
from riftlab.timeline.domain import (
    CURRENT_DECISION_STATE_SCHEMA_VERSION,
    DecisionState,
    TimelineEvent,
    TimelineReplayError,
)
from riftlab.timeline.empirical_rules import (
    BOOTS_ITEM_ID,
    BOT_LANE_QUEST_ITEM_ID,
    DEFAULT_TRINKET_ITEM_ID,
    GRANTED_ITEM_IDS_BY_RULE,
    MID_LANE_QUEST_ITEM_ID,
    RULE_R1,
    RULE_R2,
    RULE_R2I,
    RULE_R4S,
    RULE_R4W,
    RULE_R5,
    RULE_R6,
    RULE_R8,
    RULE_R8T,
    SUPPORT_ROLE,
    EmpiricalRules,
    attribute_system_purchases,
)
from riftlab.timeline.repository import SqlAlchemyTimelineRepository

SessionFactory = async_sessionmaker[AsyncSession]

STALE_FRAME_THRESHOLD_MS = 90_000

# Identifica o algoritmo que produziu DecisionState.state_version e
# MatchReconstructionMarker.reconstruction_version. Uma marca ou estado de
# versão anterior nunca é apagado nem reescrito: continua no banco, não
# colide com o que a versão corrente grava (a identidade do estado inclui
# state_version) e não conta como "já reconstruído" para a versão corrente.
# Qualquer alteração que mude o RESULTADO computado exige incrementar este
# número.
#
# Histórico:
#   1 — primeira implementação, sem marca versionada.
#   2 — marca de reconstrução passa a ser versionada; heurísticas de destroy
#       e undo viram gate operacional em dado real.
#   3 — o consumo de receita pode reivindicar unidades destruídas ANTES da
#       compra composta, no mesmo timestamp (observado em dado real; ver
#       `_consume_recipe`). Muda o inventário calculado em alguns cenários.
#   4 — `quality.preDecisionQuality` da PRIMEIRA compra de um participante
#       passa a refletir catálogo ausente, condição do patch inteiro
#       conhecida antes de qualquer evento. Estados persistidos na versão 3
#       têm essa projeção incompleta e por isso não são reaproveitados.
#   5 — o estado passa a registrar (a) o nível do participante observado no
#       MESMO frame estritamente anterior usado para o ouro
#       (`quality.levelObservation`), (b) o timestamp do próprio evento de
#       compra (`snapshot.eventTimestampMs`) e (c) o bloco RETROSPECTIVO do
#       alvo (`quality.retrospectiveTarget`: COMPLETED, REVERTED_BY_UNDO ou
#       STATUS_UNCERTAIN). Frames não são persistidos, então extrair features
#       depois não pode reabrir a fonte; estados v4 não têm esses campos e não
#       são reaproveitados.
#   6 — semântica de TRANSAÇÃO para a forma real destroy→compra: os
#       ITEM_DESTROYED do participante no instante T cujas unidades a compra
#       composta do MESMO participante em T reivindica (pool retroativo,
#       versão 3) fazem parte da transação dessa compra. O ponto de decisão
#       fica ANTES da transação: `inventory_before` inclui as unidades
#       reivindicadas e a qualidade pré-decisão não carrega as marcas desses
#       destroys; as marcas UNRECONCILED_ITEM_DESTROYED deles saem do
#       cumulativo (a remoção só vale dentro do mesmo T; decisões já
#       capturadas nunca são alteradas). Destroy em T NÃO reivindicado segue
#       conservador. Antes (v3–v5) o snapshot omitia exatamente a receita do
#       próprio alvo e toda compra composta de forma real ficava incerta.
#   7 — regras EMPÍRICAS do CC-207 (`timeline/empirical_rules.py`,
#       `configs/empirical-rules.yaml`): R1 consumo de unidade possuída,
#       R2/R2i troca de trinket e trinket inicial, R4s itens de sistema/missão,
#       R4w World Atlas por papel, R5 libera
#       UNVALIDATED_DESTROY_ORDER_RECONCILIATION, R6 undo em pilha e libera
#       UNVALIDATED_UNDO_SEMANTICS. Todas DESLIGADAS por padrão e ligadas por
#       PATCH só com evidência confirmatória registrada; com todas desligadas o
#       resultado é IDÊNTICO ao da v6 (bit a bit). Os estados passam a gravar
#       `quality.empiricalRules` (proveniência das regras ligadas no patch da
#       partida). Ativar uma regra exige nova versão (o registro recusa
#       `activatedInVersion` diferente da corrente), então estados de uma
#       versão nunca são reaproveitados com outro conjunto de regras.
RECONSTRUCTION_ALGORITHM_VERSION = 9

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


def _has_trinket_tag(item: Item | None) -> bool:
    return item is not None and "Trinket" in item.attributes.get("tags", ())


def _is_consumable_item(item: Item | None) -> bool:
    return item is not None and bool(item.flags.get("consumed"))


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


def _select_prior_frame(frames: Sequence[FrameLike], event_timestamp_ms: int) -> FrameLike | None:
    """ÚNICO critério de seleção de frame (ouro e nível usam o mesmo): o
    último frame ESTRITAMENTE anterior ao evento — nunca mesmo timestamp nem
    futuro por conveniência, nunca interpolado com o frame seguinte."""
    candidate: FrameLike | None = None
    for frame in frames:
        if frame.timestamp_ms < event_timestamp_ms and (
            candidate is None or frame.timestamp_ms > candidate.timestamp_ms
        ):
            candidate = frame
    return candidate


def select_gold_estimate(
    frames: Sequence[FrameLike], participant_id: int, event_timestamp_ms: int
) -> GoldEstimate:
    """Ouro do último frame estritamente anterior (`_select_prior_frame`).
    origin usa o vocabulário público de budget_source (docs/domain-model.md,
    shared/api/enums.py:BudgetSource) — nunca um rótulo próprio inventado
    ("OBSERVED" não é um valor do contrato)."""
    candidate = _select_prior_frame(frames, event_timestamp_ms)
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


_LEVEL_ORIGIN_FRAME = "FRAME_OBSERVATION"
_LEVEL_ORIGIN_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class LevelObservation:
    """Nível do participante do MESMO frame estritamente anterior usado para o
    ouro. Ausência é explícita e nunca vira 0: `value=None` com
    `origin=UNKNOWN` e `absent_reason` (NO_PRIOR_FRAME,
    PARTICIPANT_FRAME_MISSING, LEVEL_MISSING ou LEVEL_INVALID). Idade e
    timestamp do frame são preservados sempre que um frame anterior existe."""

    value: int | None
    age_ms: int | None
    frame_timestamp_ms: int | None
    stale: bool
    origin: str
    absent_reason: str | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "ageMs": self.age_ms,
            "frameTimestampMs": self.frame_timestamp_ms,
            "stale": self.stale,
            "origin": self.origin,
            "absentReason": self.absent_reason,
        }


def select_level_observation(
    frames: Sequence[FrameLike], participant_id: int, event_timestamp_ms: int
) -> LevelObservation:
    candidate = _select_prior_frame(frames, event_timestamp_ms)
    if candidate is None:
        return LevelObservation(None, None, None, False, _LEVEL_ORIGIN_UNKNOWN, "NO_PRIOR_FRAME")
    age_ms = event_timestamp_ms - candidate.timestamp_ms
    stale = age_ms > STALE_FRAME_THRESHOLD_MS

    def absent(reason: str) -> LevelObservation:
        return LevelObservation(
            None, age_ms, candidate.timestamp_ms, stale, _LEVEL_ORIGIN_UNKNOWN, reason
        )

    participant_frame = candidate.participant_frames.get(participant_id)
    if not participant_frame:
        return absent("PARTICIPANT_FRAME_MISSING")
    level = participant_frame.get("level")
    if level is None:
        return absent("LEVEL_MISSING")
    if isinstance(level, bool) or not isinstance(level, int) or level < 1:
        return absent("LEVEL_INVALID")
    return LevelObservation(level, age_ms, candidate.timestamp_ms, stale, _LEVEL_ORIGIN_FRAME, None)


_NO_LEVEL_OBSERVATION = LevelObservation(
    None, None, None, False, _LEVEL_ORIGIN_UNKNOWN, "NO_PRIOR_FRAME"
)

RETROSPECTIVE_COMPLETED = "COMPLETED"
RETROSPECTIVE_REVERTED_BY_UNDO = "REVERTED_BY_UNDO"
RETROSPECTIVE_STATUS_UNCERTAIN = "STATUS_UNCERTAIN"


@dataclass(frozen=True, slots=True)
class ReconstructedPurchase:
    """Uma compra elegível: snapshot é o estado ANTES de aplicar o alvo (o
    item comprado nunca aparece no próprio snapshot). Saída do reducer puro;
    a persistência (DecisionState) é responsabilidade do orquestrador.

    `uncertain`/`reasons` são CUMULATIVOS (todo o histórico até e incluindo
    esta compra, inclusive diagnósticos que dependem da identidade/receita
    DESTA MESMA compra, ex.: ITEM_UNKNOWN/ITEM_EXCLUDED_UNSUPPORTED/
    INVENTORY_CAPACITY_EXCEEDED/UNVALIDATED_DESTROY_ORDER_RECONCILIATION,
    além de NO_CATALOG marcado por evento igual às outras) -- úteis para
    transparência total e para decisões SEGUINTES, mas nunca devem ser
    usados como elegibilidade/feature/máscara PRÉ-decisão, porque contêm
    diagnósticos que só existem por causa do item ESCOLHIDO.

    `pre_decision_uncertain`/`pre_decision_reasons` são a qualidade
    ANTES de processar esta compra especificamente -- capturados no
    exato instante em que a compra começa a ser processada (mesmo ponto de
    `inventory_before`), nunca influenciados pela identidade/receita DESTE
    alvo. Inclui tanto o histórico acumulado ATÉ aqui quanto condições
    conhecidas ANTES de qualquer evento: catálogo ausente (`NO_CATALOG`)
    é uma condição do PATCH da partida inteira, fixa desde antes do replay
    começar e independente do item escolhido; por isso é injetada aqui
    mesmo na PRIMEIRA compra, nunca só a partir do que já foi acumulado.
    É isto que deve alimentar elegibilidade/feature/máscara pré-compra;
    comparar a MESMA posição de decisão com alvos diferentes (mesmo
    prefixo) preserva estes dois campos byte-a-byte, mesmo quando
    `uncertain`/`reasons`/`destroy_order_reconciliation_used`
    legitimamente divergem por causa do alvo escolhido.

    `event_timestamp_ms` e `level_observation` são conhecidos no instante da
    decisão (o nível vem do mesmo frame estritamente anterior do ouro).

    `retrospective_*` é um bloco RETROSPECTIVO, separado: registra o que
    aconteceu com o alvo DEPOIS da decisão (`COMPLETED`, `REVERTED_BY_UNDO`
    quando um undo correlacionado pelo reducer desfaz esta compra, ou
    `STATUS_UNCERTAIN` quando um undo não correlacionado posterior pode ter
    desfeito a compra mais recente ainda não revertida — conservador). Serve
    só à seleção retrospectiva do ponto de decisão: nunca alimenta features,
    máscaras, `pre_decision_*`, `inventory_before`, `gold_estimate` nem
    `level_observation`, de nenhuma compra."""

    event_source_index: int
    participant_id: int
    inventory_before: InventorySnapshot
    purchased_item_id: int
    gold_estimate: GoldEstimate
    uncertain: bool
    reasons: tuple[UncertainReason, ...]
    destroy_reconciliation_heuristic_used: bool
    destroy_order_reconciliation_used: bool = False
    pre_decision_uncertain: bool = False
    pre_decision_reasons: tuple[UncertainReason, ...] = ()
    destroy_order_claimed_source_indexes: tuple[int, ...] = ()
    event_timestamp_ms: int = 0
    level_observation: LevelObservation = _NO_LEVEL_OBSERVATION
    retrospective_status: str = RETROSPECTIVE_COMPLETED
    retrospective_undo_source_index: int | None = None
    retrospective_correlation_unvalidated: bool = False


_REVERSIBLE_OP_PURCHASE = "PURCHASE"
_REVERSIBLE_OP_SALE = "SALE"


@dataclass(frozen=True, slots=True)
class _ReversibleOp:
    """Ledger da última OPERAÇÃO reversível do participante (compra OU
    venda). Guarda o multiconjunto EXATO anterior à operação, não uma
    reconstrução aproximada a partir de beforeId/afterId — undo restaura
    esse valor diretamente quando a correlação é única, nunca "repara" a
    partir do inventário final. consumed_components (vazio para venda/item
    sem receita) é o conjunto de invariantes que afterId precisa respeitar
    quando operation_type é PURCHASE: comparar só beforeId não comprova
    correlação (beforeId=999 seria aceito mesmo sem nenhum componente 999
    envolvido).

    operation_type existe porque, no pequeno corpus real analisado
    (10 partidas, patches 14.20 e 15.1), undo de compra e undo de venda
    usam formas OPOSTAS de beforeId/afterId — undo de compra:
    beforeId=<item comprado>, afterId=0 (ou componente consumido); undo de
    venda: beforeId=0, afterId=<item vendido>. Comparar sempre contra
    beforeId nunca correlacionaria um undo de venda: os 16 undos de venda
    observados ficariam todos sem correlação por essa cegueira estrutural.
    Guardar o tipo explicitamente no ledger (em vez de inferir só por
    resulting_item_id) permite comparar cada tipo pela sua própria forma.
    Isso descreve a forma encontrada nessa amostra; NÃO é a semântica
    oficial da Riot nem foi verificado para outros patches, por isso a
    correlação continua marcando uncertain=True em dado REAL (ver
    reconstruct_participant_purchases)."""

    resulting_item_id: int
    operation_type: str
    prior_inventory: InventorySnapshot
    consumed_components: tuple[tuple[int, int], ...] = ()
    timestamp_ms: int = 0
    purchase_source_index: int | None = None


def _is_claimed_destroy_mark(reason: UncertainReason, claimed_indexes: set[int]) -> bool:
    """Marca provisória de um destroy que a compra atual reivindica (v6)."""
    return (
        reason.code == "UNRECONCILED_ITEM_DESTROYED"
        and reason.event_source_index in claimed_indexes
    )


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
    backward_destroy_pool: dict[int, list[int]],
    backward_claimed_log: list[tuple[int, int]],
) -> InventorySnapshot:
    """Consome a receita de `item` recursivamente. Nunca consome o mesmo
    ancestral duas vezes, pois cada remoção já muta `inventory` antes do
    próximo componente ser avaliado. Sem receita conhecida para decompor, o
    restante é só pago em ouro (não modelado aqui), igual à compra direta
    de um item base.

    `backward_destroy_pool`: por item_id, a lista (em ordem, FIFO) de
    `source_index` de unidades destruídas MAIS CEDO no MESMO timestamp
    (populada por `reconstruct_participant_purchases`, nunca por esta
    função) e ainda não reivindicadas por nenhuma outra compra composta
    deste mesmo instante. Existe porque, no corpus real analisado (10
    partidas, patches 14.20 e 15.1), todos os 851 ITEM_DESTROYED que têm
    compra no mesmo timestamp precedem essa compra na ordem dos eventos:
    os componentes são destruídos primeiro, o item composto surge depois.

    O pool é consultado PRIMEIRO: reivindicar uma unidade já destruída
    (evidência mais específica, destruída exatamente neste instante) é
    preferido a consumir uma unidade que segue possuída e intocada; do
    contrário a mesma unidade seria removida duas vezes e o inventário
    ficaria menor do que a evidência sustenta. Só consulta `inventory`
    para a quantidade AINDA faltante depois de esgotar o pool -- nunca
    assume que todo destroy co-temporal pertence à receita (só reivindica
    até `needed`, nunca mais, e só do `item_id` exato). Nunca remove nada
    de `inventory` pela reivindicação em si (essas unidades já foram
    removidas quando o destroy foi processado) -- só esvazia a lista do
    pool e registra `(component_id, destroy_source_index)` por UNIDADE
    reivindicada, para contar cobertura por evento e não só por compra.
    Reivindicação nunca gera uma nova expectativa de destroy futura via
    `pending_destroys` (`backward_claimed_log` é distinto de
    `consumed_log`) -- o destroy já aconteceu, no passado."""
    for component in item.recipe:
        component_id = int(component["itemId"])
        needed = int(component["quantity"])

        available = backward_destroy_pool.get(component_id) or []
        backward_take = min(len(available), needed)
        if backward_take > 0:
            claimed, remaining = available[:backward_take], available[backward_take:]
            backward_destroy_pool[component_id] = remaining
            backward_claimed_log.extend((component_id, source_index) for source_index in claimed)
            needed -= backward_take

        if needed > 0:
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
                    inventory = _consume_recipe(
                        inventory,
                        sub_item,
                        catalog_items,
                        consumed_log,
                        backward_destroy_pool,
                        backward_claimed_log,
                    )
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


def _lane_quest_boot_successor(
    item_id: int,
    timestamp_ms: int,
    quest_ts: Mapping[int, set[int]],
    catalog_items: Mapping[int, Item],
) -> int | None:
    """R8: 0 = bota removida (missão BOTTOM); ID = sucessor (missão MID); None = não se aplica.

    Bota = Botas (1001) ou item cuja receita contém as Botas. O sucessor do MID é o
    item único do catálogo cuja receita é exatamente uma unidade da bota destruída.
    """
    item = catalog_items.get(item_id)
    recipe = _recipe_ids(item)
    is_boot = item_id == BOOTS_ITEM_ID or BOOTS_ITEM_ID in recipe
    if not is_boot:
        return None
    if timestamp_ms in quest_ts.get(BOT_LANE_QUEST_ITEM_ID, set()):
        return 0
    if timestamp_ms in quest_ts.get(MID_LANE_QUEST_ITEM_ID, set()) and item_id != BOOTS_ITEM_ID:
        successors = [
            other_id
            for other_id, other in catalog_items.items()
            if isinstance(other.recipe, list)
            and len(other.recipe) == 1
            and isinstance(other.recipe[0], dict)
            and other.recipe[0].get("itemId") == item_id
            and other.recipe[0].get("quantity") == 1
        ]
        return successors[0] if len(successors) == 1 else None
    return None


def _recipe_ids(item: Item | None) -> set[int]:
    if item is None or not isinstance(item.recipe, list):
        return set()
    return {
        entry["itemId"]
        for entry in item.recipe
        if isinstance(entry, dict) and isinstance(entry.get("itemId"), int)
    }


def reconstruct_participant_purchases(
    *,
    participant_id: int,
    events: Sequence[TimelineEvent],
    origin: SourceOrigin,
    frames: Sequence[FrameLike] = (),
    catalog_id: uuid.UUID | None,
    catalog_items: Mapping[int, Item] | None = None,
    excluded_item_ids: frozenset[int] = frozenset(),
    enabled_rules: frozenset[str] = frozenset(),
    system_item_ids: frozenset[int] = frozenset(),
) -> list[ReconstructedPurchase]:
    """Reducer puro. events deve conter os eventos de TODOS os participantes
    da partida — a filtragem por dono acontece aqui (nunca antes de chamar),
    para que dois participantes processados separadamente fiquem isolados
    por construção, sem se misturar. catalog_id=None é modo degradado
    explícito (sem catálogo resolvido para o patch da partida): toda compra
    vira INVENTORY_UNCERTAIN (NO_CATALOG), mas o replay continua
    melhor-esforço por item_id literal — sem catálogo não há receita
    conhecida para consumir, então nenhum componente é removido.

    origin distingue dado sintético (SourceOrigin.CURATED_DEMO) de dado real
    (COMPLETED_MATCH) — obrigatório, nunca um default silencioso:
    reconciliação de ITEM_DESTROYED por timestamp/item/quantidade e
    correlação de ITEM_UNDO por beforeId/afterId são HIPÓTESES próprias
    sobre semântica não documentada da Riot. Foram examinadas num corpus
    real pequeno (10 partidas, patches 14.20 e 15.1) e são consistentes
    com ele, mas não foram confirmadas como semântica oficial nem
    verificadas em outros patches. Um resultado DEMO pode legitimamente
    ficar `uncertain=False` quando essas hipóteses se aplicam de forma
    limpa — a regra é sintética e sua correção É verificada pela suíte de
    testes. Um resultado de dado REAL nunca pode: aplicar qualquer uma das
    hipóteses em dado real sempre marca o participante incerto a partir
    dali, mesmo quando a correlação "bate" segundo a regra interna — bater
    com uma regra interna não comprova a semântica real.

    enabled_rules (v7, CC-207): regras EMPÍRICAS ligadas para o patch desta
    partida (`EmpiricalRules.enabled_for`); vazio = comportamento idêntico ao
    da v6. system_item_ids só vale com R4s. O reducer continua puro: as
    regras entram como parâmetro, nunca lidas de arquivo aqui."""
    catalog_items = catalog_items if catalog_items is not None else {}
    catalog_available = catalog_id is not None
    is_demo = origin is SourceOrigin.CURATED_DEMO

    owned_events = sorted(
        (
            event
            for event in events
            if event.type in _RELEVANT_EVENT_TYPES
            and event.payload.get("participantId") == participant_id
        ),
        key=lambda event: (event.timestamp_ms, event.source_index),
    )

    # Pré-cálculos das regras empíricas (puros; sem efeito com as regras desligadas).
    trinket_purchase_ts = {
        event.timestamp_ms
        for event in owned_events
        if event.type == "ITEM_PURCHASED"
        and isinstance(event.payload.get("itemId"), int)
        and _has_trinket_tag(catalog_items.get(event.payload["itemId"]))
    }
    purchase_ts = {event.timestamp_ms for event in owned_events if event.type == "ITEM_PURCHASED"}
    # R7*: itens concedidos sem compra; R8: instantes em que a missão de rota terminou.
    granted_item_ids = frozenset().union(
        *(ids for rule, ids in GRANTED_ITEM_IDS_BY_RULE.items() if rule in enabled_rules)
    )
    quest_ts = {
        quest_id: {
            event.timestamp_ms
            for event in owned_events
            if event.type == "ITEM_DESTROYED" and event.payload.get("itemId") == quest_id
        }
        for quest_id in (BOT_LANE_QUEST_ITEM_ID, MID_LANE_QUEST_ITEM_ID)
    }
    undo_stack_enabled = RULE_R6 in enabled_rules
    rev_stack: list[_ReversibleOp] = []
    # R2i: todo participante começa com o trinket padrão (65/66 dos que nunca
    # compraram trinket terminaram com ele).
    inventory = (
        InventorySnapshot(trinket_item_id=DEFAULT_TRINKET_ITEM_ID)
        if RULE_R2I in enabled_rules
        else EMPTY_INVENTORY
    )
    uncertain = False
    reasons: list[UncertainReason] = []
    destroy_heuristic_used = False
    destroy_order_heuristic_used = False
    last_reversible: _ReversibleOp | None = None
    # pending_destroys[timestamp_ms][component_item_id] = unidades consumidas
    # por uma compra de receita neste mesmo instante, ainda não confirmadas
    # por um ITEM_DESTROYED real — nunca remove a mesma unidade duas vezes.
    pending_destroys: dict[int, dict[int, int]] = {}
    # backward_destroy_pool[item_id] = source_index (em ordem) das unidades
    # destruídas MAIS CEDO no timestamp_ms ATUAL (current_timestamp_ms),
    # ainda não reivindicadas por nenhuma compra composta deste mesmo
    # instante. Resetado sempre que timestamp_ms muda -- nunca cruza
    # fronteira de instante, então só contém destroys anteriores no
    # processamento (por construção: só populado quando um ITEM_DESTROYED
    # é processado, e eventos são processados em ordem) a qualquer compra
    # que o consulte. Lista (não contagem): precisa da IDENTIDADE de cada
    # unidade destruída para contar cobertura por evento/unidade, nunca só
    # um total por compra.
    backward_destroy_pool: dict[int, list[int]] = {}
    current_timestamp_ms: int | None = None
    results: list[ReconstructedPurchase] = []
    # Bloco RETROSPECTIVO do alvo (ver ReconstructedPurchase): anotado só
    # DEPOIS da decisão, a partir da correlação de undo que este mesmo reducer
    # já faz (fonte única) e aplicado aos resultados no fim — nunca lido por
    # nenhum campo pré-decisão. purchase_order guarda os source_index das
    # compras já processadas, em ordem.
    purchase_order: list[int] = []
    retro_status: dict[int, str] = {}
    retro_undo_index: dict[int, int] = {}

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
        prev_top = last_reversible
        if event.timestamp_ms != current_timestamp_ms:
            current_timestamp_ms = event.timestamp_ms
            backward_destroy_pool = {}

        if event.type == "ITEM_PURCHASED":
            item_id = _require_int_field(event, "itemId")
            # Estado ao chegar nesta compra, com os ITEM_DESTROYED anteriores
            # (inclusive os do mesmo instante T) já aplicados provisoriamente.
            # `before` (o ponto de decisão) só é definido DEPOIS de saber quais
            # destroys esta compra reivindica: os reivindicados fazem parte da
            # TRANSAÇÃO da compra (v6), então o ponto de decisão fica antes
            # deles. Os não reivindicados seguem aplicados (conservador).
            inventory_on_arrival = inventory
            reasons_on_arrival = tuple(reasons)
            consumed: list[tuple[int, int]] = []
            backward_claimed: list[tuple[int, int]] = []

            if not catalog_available:
                _mark_uncertain("NO_CATALOG", event, item_id)
                inventory = inventory.with_added(item_id, 1)
            elif item_id in catalog_items:
                item = catalog_items[item_id]
                new_inventory = _consume_recipe(
                    inventory,
                    item,
                    catalog_items,
                    consumed,
                    backward_destroy_pool,
                    backward_claimed,
                )
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

            # S1/S2 (v6): destroys reivindicados por esta compra fazem parte da
            # sua transação. (S1) o ponto de decisão fica ANTES deles: as
            # unidades reivindicadas voltam ao `before`; (S2) as marcas
            # provisórias UNRECONCILED_ITEM_DESTROYED desses destroys saem do
            # cumulativo — tudo no MESMO instante T, e só para o que esta
            # compra de fato reivindica (destroy não reivindicado mantém a
            # marca e a remoção). Decisões já capturadas nunca são alteradas:
            # `reasons_on_arrival` e os resultados anteriores são imutáveis.
            claimed_indexes = {source_index for _, source_index in backward_claimed}
            before = inventory_on_arrival
            for claimed_item_id, _ in backward_claimed:
                before = before.with_added(claimed_item_id, 1)
            if claimed_indexes:
                reasons[:] = [
                    r for r in reasons if not _is_claimed_destroy_mark(r, claimed_indexes)
                ]
                uncertain = bool(reasons)
                base_pre_reasons = tuple(
                    r
                    for r in reasons_on_arrival
                    if not _is_claimed_destroy_mark(r, claimed_indexes)
                )
            else:
                base_pre_reasons = reasons_on_arrival

            # Qualidade ANTES da transação desta compra: o histórico até aqui
            # sem as marcas dos destroys reivindicados, nunca influenciada pela
            # identidade/receita DESTE alvo além do que a própria transação
            # consome. ITEM_UNKNOWN/ITEM_EXCLUDED_UNSUPPORTED/
            # INVENTORY_CAPACITY_EXCEEDED/
            # UNVALIDATED_DESTROY_ORDER_RECONCILIATION abaixo dependem do item
            # ESCOLHIDO (pós-transição, nunca elegibilidade/feature/máscara
            # pré-decisão).
            #
            # `catalog_available` é uma condição do PATCH/catálogo resolvido
            # para a partida inteira -- fixa para toda a chamada, conhecida
            # ANTES de qualquer evento ser processado, nunca dependente de
            # qual item é comprado. Por isso é injetada aqui
            # INCONDICIONALMENTE quando ausente -- inclusive na PRIMEIRA
            # compra, nunca só a partir do que já foi acumulado em
            # `reasons` até agora (que seria vazio na primeira compra,
            # escondendo uma condição já conhecida desde antes do replay
            # começar).
            pre_decision_uncertain = bool(base_pre_reasons) or not catalog_available
            pre_decision_reasons = base_pre_reasons
            if not catalog_available:
                pre_decision_reasons = (
                    *pre_decision_reasons,
                    UncertainReason(
                        code="NO_CATALOG",
                        event_source_index=event.source_index,
                        timestamp_ms=event.timestamp_ms,
                        item_id=None,
                    ),
                )

            # Capacidade verificada sempre (não só no caminho de catálogo
            # conhecido): um item desconhecido/excluído/sem catálogo também
            # ocupa uma slot normal (trinket nunca entra em `.items`, então
            # esta checagem é naturalmente um no-op para ele).
            slots, stack_unknown = _slots_used(inventory, catalog_items)
            if stack_unknown:
                _mark_uncertain("ITEM_STACK_RULE_UNKNOWN", event, item_id)
            elif slots > _MAX_NORMAL_SLOTS:
                _mark_uncertain("INVENTORY_CAPACITY_EXCEEDED", event, item_id)

            if backward_claimed:
                # Componente(s) reivindicado(s) de destroy(s) anteriores no
                # MESMO timestamp -- mesma disciplina de gate das outras
                # heurísticas: bater com esta hipótese não comprova a
                # semântica real, então dado REAL sempre marca incerto a
                # partir daqui via este código; DEMO nunca marca este código
                # específico se a regra é sintética e testada. Desde a v6, as
                # marcas provisórias UNRECONCILED_ITEM_DESTROYED dos destroys
                # reivindicados já saíram do cumulativo (acima), então em DEMO
                # a compra fica sem nenhuma marca por eles; um destroy NÃO
                # reivindicado mantém a sua (conservador, nunca curado).
                destroy_order_heuristic_used = True
                if not is_demo and RULE_R5 not in enabled_rules:
                    _mark_uncertain("UNVALIDATED_DESTROY_ORDER_RECONCILIATION", event, item_id)

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
                    destroy_order_reconciliation_used=destroy_order_heuristic_used,
                    pre_decision_uncertain=pre_decision_uncertain,
                    pre_decision_reasons=pre_decision_reasons,
                    destroy_order_claimed_source_indexes=tuple(
                        source_index for _, source_index in backward_claimed
                    ),
                    event_timestamp_ms=event.timestamp_ms,
                    level_observation=select_level_observation(
                        frames, participant_id, event.timestamp_ms
                    ),
                )
            )
            purchase_order.append(event.source_index)
            retro_status[event.source_index] = RETROSPECTIVE_COMPLETED
            last_reversible = _ReversibleOp(
                resulting_item_id=item_id,
                operation_type=_REVERSIBLE_OP_PURCHASE,
                prior_inventory=before,
                consumed_components=tuple(consumed),
                timestamp_ms=event.timestamp_ms,
                purchase_source_index=event.source_index,
            )

        elif event.type == "ITEM_SOLD":
            item_id = _require_int_field(event, "itemId")
            before = inventory
            if inventory.trinket_item_id == item_id:
                inventory = inventory.with_trinket(None)
                last_reversible = _ReversibleOp(
                    resulting_item_id=item_id,
                    operation_type=_REVERSIBLE_OP_SALE,
                    prior_inventory=before,
                    timestamp_ms=event.timestamp_ms,
                )
            elif inventory.owned(item_id) > 0:
                inventory = inventory.with_removed(item_id, 1)
                # venda também é reversível (revisão: "venda sempre apaga o
                # ledger" deixava undo de venda sem caminho suportado, contra
                # o plano) — resulting_item_id é o item vendido; a forma real
                # de beforeId/afterId de um undo de venda é distinta da de
                # compra (ver operation_type em _ReversibleOp).
                last_reversible = _ReversibleOp(
                    resulting_item_id=item_id,
                    operation_type=_REVERSIBLE_OP_SALE,
                    prior_inventory=before,
                    timestamp_ms=event.timestamp_ms,
                )
            else:
                _mark_uncertain("SALE_WITHOUT_OWNERSHIP", event, item_id)

        elif event.type == "ITEM_DESTROYED":
            item_id = _require_int_field(event, "itemId")
            pending = pending_destroys.get(event.timestamp_ms)
            if pending and pending.get(item_id, 0) > 0:
                # Reconciliado por heurística de timestamp/item/quantidade:
                # NUNCA removido de novo (evita dupla contagem — correto
                # independente de a correlação ser semanticamente real ou
                # não, então preservado incondicionalmente). Mas bater com
                # esta regra interna não comprova a semântica real: um
                # sinalizador só de documentação não bloqueia nada, então em
                # dado REAL aplicar esta hipótese sempre marca o
                # participante incerto a partir daqui; em DEMO, a regra é
                # sintética e verificada pela suíte de testes, então pode
                # ficar certa.
                pending[item_id] -= 1
                destroy_heuristic_used = True
                if not is_demo:
                    _mark_uncertain("UNVALIDATED_DESTROY_RECONCILIATION", event, item_id)
            elif (
                RULE_R4S in enabled_rules
                and item_id in system_item_ids
                and inventory.owned(item_id) == 0
                and inventory.trinket_item_id != item_id
            ):
                # R4s: item de SISTEMA/MISSÃO (nunca comprado, nunca em slot
                # normal): o destroy sem posse não altera o inventário e não
                # marca incerteza. Comprado e depois destruído (posse) segue o
                # caminho normal.
                last_reversible = None
            elif (
                item_id in granted_item_ids
                and inventory.owned(item_id) == 0
                and inventory.trinket_item_id != item_id
            ):
                # R7b/R7m/R7x: item concedido por runa (nunca comprado): o
                # destroy sem posse é o consumo da unidade concedida. A unidade
                # nunca foi contada no inventário, então nada é removido.
                last_reversible = None
            elif (
                (RULE_R8T if item_id == BOOTS_ITEM_ID else RULE_R8) in enabled_rules
                and inventory.owned(item_id) > 0
                and _lane_quest_boot_successor(item_id, event.timestamp_ms, quest_ts, catalog_items)
                is not None
            ):
                # R8 (tier 2) / R8t (Botas 1001, só BOTTOM): bota destruída no instante
                # em que a missão de rota termina. BOTTOM: a bota sai sem substituta.
                # MID: a bota tier 2 vira o sucessor único do catálogo (concedido).
                successor = _lane_quest_boot_successor(
                    item_id, event.timestamp_ms, quest_ts, catalog_items
                )
                inventory = inventory.with_removed(item_id, 1)
                if successor:
                    inventory = inventory.with_added(successor, 1)
                last_reversible = None
            else:
                # R1: destroy de consumível POSSUÍDO, sem compra composta que o
                # reivindique, é consumo de UMA unidade (a remoção e o pool
                # retroativo abaixo continuam iguais). Sem posse segue marcando.
                # R2: destroy do trinket possuído no MESMO instante de uma compra
                # de trinket do participante é a troca de slot (só a forma
                # observada: destroy ANTES da compra).
                r1_consume = (
                    RULE_R1 in enabled_rules
                    and _is_consumable_item(catalog_items.get(item_id))
                    and inventory.trinket_item_id != item_id
                    and inventory.owned(item_id) > 0
                )
                r2_swap = (
                    RULE_R2 in enabled_rules
                    and event.timestamp_ms in trinket_purchase_ts
                    and inventory.trinket_item_id == item_id
                )
                if not (r1_consume or r2_swap):
                    _mark_uncertain("UNRECONCILED_ITEM_DESTROYED", event, item_id)
                if inventory.trinket_item_id == item_id:
                    inventory = inventory.with_trinket(None)
                elif inventory.owned(item_id) > 0:
                    inventory = inventory.with_removed(item_id, 1)
                    # Unidade real, possuída e removida agora (nunca para
                    # trinket, nunca sem posse) fica disponível para uma
                    # compra composta POSTERIOR no MESMO timestamp
                    # reivindicar (_consume_recipe). Nunca reivindicada duas
                    # vezes -- removida da lista no momento da
                    # reivindicação, resetada a cada mudança de timestamp.
                    # Identidade (source_index) preservada, não só uma
                    # contagem.
                    backward_destroy_pool.setdefault(item_id, []).append(event.source_index)
                else:
                    _mark_uncertain("DESTROY_WITHOUT_OWNERSHIP", event, item_id)
                last_reversible = None

        elif event.type == "ITEM_UNDO":
            before_id = _require_int_field(event, "beforeId")
            after_id = _require_int_field(event, "afterId")
            correlated = False
            if last_reversible is not None:
                if last_reversible.operation_type == _REVERSIBLE_OP_PURCHASE:
                    valid_after_ids = {0} | {
                        component_id for component_id, _ in last_reversible.consumed_components
                    }
                    # Comparar só beforeId não comprova correlação — beforeId
                    # batendo com afterId contraditório (ex.: item nunca
                    # consumido por esta operação) nunca é aplicado às
                    # cegas, mesmo com beforeId correto.
                    correlated = (
                        before_id == last_reversible.resulting_item_id
                        and after_id in valid_after_ids
                    )
                elif last_reversible.operation_type == _REVERSIBLE_OP_SALE:
                    # Forma observada no corpus real analisado (ver
                    # _ReversibleOp): undo de venda usa beforeId=0/
                    # afterId=<item vendido>, nunca a forma de undo de
                    # compra.
                    correlated = before_id == 0 and after_id == last_reversible.resulting_item_id
            if not correlated:
                _mark_uncertain("UNDO_UNCORRELATED", event, before_id)
                # Conservador: um undo sem correlação pode ter desfeito a
                # compra mais recente do participante que ainda consta como
                # COMPLETED (compras já revertidas ou já incertas ficam como
                # estão). Só afeta o bloco retrospectivo, nunca o estado
                # pré-decisão de nenhuma compra.
                for candidate_index in reversed(purchase_order):
                    if retro_status[candidate_index] == RETROSPECTIVE_COMPLETED:
                        retro_status[candidate_index] = RETROSPECTIVE_STATUS_UNCERTAIN
                        retro_undo_index[candidate_index] = event.source_index
                        break
            else:
                # correlated só é True dentro do bloco `if last_reversible is
                # not None` acima -- nunca alcançável com last_reversible=None.
                assert last_reversible is not None
                if (
                    last_reversible.operation_type == _REVERSIBLE_OP_PURCHASE
                    and last_reversible.purchase_source_index is not None
                ):
                    retro_status[last_reversible.purchase_source_index] = (
                        RETROSPECTIVE_REVERTED_BY_UNDO
                    )
                    retro_undo_index[last_reversible.purchase_source_index] = event.source_index
                inventory = last_reversible.prior_inventory
                _discard_pending(last_reversible.consumed_components, last_reversible.timestamp_ms)
                last_reversible = None
                # Validar afterId contra {0, componentes consumidos} é um
                # invariante interno deste reducer, não uma semântica real
                # confirmada — bater com a regra não comprova a correlação de
                # verdade. Em dado REAL, todo undo aplicado (mesmo
                # "correlacionado") marca incerto a partir daqui; em DEMO, a
                # regra é sintética e testada.
                if not is_demo and RULE_R6 not in enabled_rules:
                    _mark_uncertain("UNVALIDATED_UNDO_SEMANTICS", event, before_id)

        if undo_stack_enabled:
            if (
                event.type in ("ITEM_PURCHASED", "ITEM_SOLD")
                and last_reversible is not None
                and last_reversible is not prev_top
            ):
                rev_stack.append(last_reversible)
            elif event.type == "ITEM_DESTROYED":
                # R6: destroys no MESMO instante de uma compra do participante
                # fazem parte da transação dela.
                if event.timestamp_ms not in purchase_ts:
                    rev_stack.clear()
            elif event.type == "ITEM_UNDO" and prev_top is not None and last_reversible is None:
                # undo correlacionado (o ramo acima zera `last_reversible`): o
                # próximo undo se correlaciona com a operação ANTERIOR (pilha).
                if rev_stack:
                    rev_stack.pop()
                last_reversible = rev_stack[-1] if rev_stack else None

    return [
        replace(
            purchase,
            retrospective_status=retro_status[purchase.event_source_index],
            retrospective_undo_source_index=retro_undo_index.get(purchase.event_source_index),
            # Em dado REAL a reversão vem de uma correlação interna nunca
            # validada (mesma disciplina das outras heurísticas).
            retrospective_correlation_unvalidated=(
                retro_status[purchase.event_source_index] == RETROSPECTIVE_REVERTED_BY_UNDO
                and not is_demo
            ),
        )
        for purchase in results
    ]


def _decision_state_from_purchase(
    match_id: str,
    catalog_id: uuid.UUID | None,
    origin: SourceOrigin,
    purchase: ReconstructedPurchase,
    empirical_rules: Sequence[dict[str, Any]] = (),
) -> DecisionState:
    return DecisionState(
        id=uuid.uuid4(),
        match_id=match_id,
        participant_id=purchase.participant_id,
        event_source_index=purchase.event_source_index,
        state_version=RECONSTRUCTION_ALGORITHM_VERSION,
        snapshot={
            "inventory": purchase.inventory_before.to_payload(),
            "purchasedItemId": purchase.purchased_item_id,
            # Instante do próprio evento de compra: conhecido na decisão; útil
            # para ordenação/paginação sem join com timeline_events.
            "eventTimestampMs": purchase.event_timestamp_ms,
        },
        features={},  # seleção/extração de features é CC-204, não este card
        quality={
            "uncertain": purchase.uncertain,
            "reasons": [reason.to_payload() for reason in purchase.reasons],
            "goldEstimate": purchase.gold_estimate.to_payload(),
            # Nível do MESMO frame estritamente anterior do ouro (frames não
            # são persistidos): valor, idade, timestamp do frame e ausência
            # explícita, nunca 0.
            "levelObservation": purchase.level_observation.to_payload(),
            # Bloco RETROSPECTIVO do alvo: o que aconteceu com a compra
            # DEPOIS da decisão. Serve só à seleção retrospectiva; nunca
            # alimenta features, máscaras nem preDecisionQuality.
            "retrospectiveTarget": {
                "status": purchase.retrospective_status,
                "undoSourceIndex": purchase.retrospective_undo_source_index,
                "correlationUnvalidated": purchase.retrospective_correlation_unvalidated,
            },
            "sourceOrigin": origin.value,
            # Proveniência (CC-207, v7): regras EMPÍRICAS ligadas no patch desta
            # partida (vazio = v6). Informativo: nada de elegibilidade/feature/
            # máscara lê este bloco.
            "empiricalRules": [dict(rule) for rule in empirical_rules],
            # `uncertain`/`reasons` acima são CUMULATIVOS e incluem
            # diagnósticos que dependem da identidade/receita DESTE alvo
            # (ITEM_UNKNOWN/ITEM_EXCLUDED_UNSUPPORTED/
            # INVENTORY_CAPACITY_EXCEEDED/
            # UNVALIDATED_DESTROY_ORDER_RECONCILIATION) — nunca devem
            # alimentar elegibilidade/feature/máscara PRÉ-decisão.
            # `preDecisionQuality` é a qualidade ANTES de processar esta
            # compra especificamente, nunca influenciada pelo alvo — é isto
            # que elegibilidade/feature/máscara pré-compra deve consultar.
            # Inclui catálogo ausente desde a PRIMEIRA compra (NO_CATALOG é
            # condição do patch da partida inteira, conhecida antes de
            # qualquer evento, nunca dependente do item escolhido).
            "preDecisionQuality": {
                "uncertain": purchase.pre_decision_uncertain,
                "reasons": [reason.to_payload() for reason in purchase.pre_decision_reasons],
            },
            # Bater com a heurística de reconciliação NUNCA é certeza fora de
            # DEMO — em dado real, um destroy reconciliado também aparece em
            # `reasons` (UNVALIDATED_DESTROY_RECONCILIATION) e marca
            # `uncertain=True` a partir daqui; este bloco é só a
            # documentação factual de QUE a heurística foi usada, nunca o
            # único lugar que bloqueia elegibilidade.
            "destroyReconciliationHeuristic": {
                "appliedToThisParticipant": purchase.destroy_reconciliation_heuristic_used,
                "validatedAgainstRealCorpus": False,
                "note": (
                    "correlação por timestamp_ms/item_id/quantidade entre compra de "
                    "receita e ITEM_DESTROYED — examinada num corpus real pequeno "
                    "(10 partidas, patches 14.20/15.1), nunca confirmada como "
                    "semântica oficial da Riot nem verificada nos patches 16.16/16.17 "
                    "do recorte congelado — estimativa, não certeza. Fora do modo demo, "
                    "aplicar esta heurística também marca o participante "
                    "uncertain=True (ver reasons)."
                ),
            },
            # Mesma disciplina da heurística acima, para destroy ANTES da
            # compra composta no mesmo timestamp (no corpus real analisado,
            # 851/851 destroys com compra co-temporal precedem a compra).
            # `claimedDestroySourceIndexes` são os source_index dos
            # ITEM_DESTROYED efetivamente reivindicados POR ESTA compra
            # (nunca cumulativo, nunca outra compra) — usar para contar
            # cobertura por EVENTO/unidade, nunca por compra (uma compra
            # pode reivindicar várias unidades, ou nenhuma, então contar
            # compras não mede destroys).
            "destroyOrderReconciliationHeuristic": {
                "appliedToThisParticipant": purchase.destroy_order_reconciliation_used,
                "claimedDestroySourceIndexes": list(purchase.destroy_order_claimed_source_indexes),
                "validatedAgainstRealCorpus": False,
                "note": (
                    "unidade destruída ANTES da compra composta, no mesmo timestamp, "
                    "reivindicada como consumo de receita — mesma investigação empírica "
                    "e mesmas ressalvas de destroyReconciliationHeuristic acima."
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
    *,
    empirical_rules: EmpiricalRules | None = None,
) -> MatchReconstruction:
    """Orquestrador (CC-203): busca (banco+fonte local, fora de transação de
    escrita) -> reduz (puro, um participante por vez) -> persiste (uma única
    transação, todos os participantes juntos ou nada). Idempotente via marca
    explícita `match_reconstructions` (checar só `list_decision_states` de um
    participante falharia quando esse participante não tivesse compra
    alguma) versionada por `RECONSTRUCTION_ALGORITHM_VERSION`: uma marca de
    versão antiga nunca conta como "já feita" para a versão atual, e
    `DecisionState`s de uma versão anterior nunca são apagados/sobrescritos.
    A marca é consultada ANTES de qualquer I/O de fonte: um resultado já
    persistido nunca depende de reabrir raw/fixture — só a reconstrução
    FRESCA (marca ausente) precisa da fonte, para
    frames/participantes/conferência de integridade. Catálogo é resolvido e
    fixado uma única vez aqui; a reexecução idempotente devolve o
    catalog_id REALMENTE usado (da marca persistida), nunca um valor
    reresolvido na hora — mesmo que uma sincronização concorrente já tenha
    trocado o canônico atual (catalog_id obtido continua consultável por ID
    exato e imutável, docs/domain-model.md).

    `empirical_rules` (v7): registro de regras empíricas; None = todas
    desligadas (comportamento da v6). As regras ligadas são resolvidas pelo
    PATCH da partida."""
    rules = empirical_rules if empirical_rules is not None else EmpiricalRules.disabled()
    ingestion_repo_factory = SqlAlchemyIngestionRepository
    timeline_repo_factory = SqlAlchemyTimelineRepository
    catalog_repo_factory = SqlAlchemyCatalogRepository

    async with unit_of_work(session_factory) as session:
        match = await ingestion_repo_factory(session).get_match(match_id)
    if match is None:
        raise NotFoundError(f"match {match_id!r} não encontrado; importar antes de reconstruir")

    async with unit_of_work(session_factory) as session:
        marker = await timeline_repo_factory(session).get_match_reconstruction(
            match_id, RECONSTRUCTION_ALGORITHM_VERSION
        )
    if marker is not None:
        # Caminho idempotente: nunca reabre a fonte (fixture/raw) — lista
        # participantes já persistidos (CC-201) e os estados já computados
        # NESSA versão do algoritmo, sem tocar match_source (testado com uma
        # fonte que levanta se chamada).
        async with unit_of_work(session_factory) as session:
            participants = await ingestion_repo_factory(session).list_participants(match_id)
            repo = timeline_repo_factory(session)
            all_existing: list[DecisionState] = []
            for participant in participants:
                all_existing.extend(
                    state
                    for state in await repo.list_decision_states(
                        match_id, participant.participant_id
                    )
                    if state.state_version == RECONSTRUCTION_ALGORITHM_VERSION
                )
        return MatchReconstruction(
            match_id=match_id, catalog_id=marker.catalog_id, decision_states=all_existing
        )

    # Reconstrução FRESCA (marca ausente para esta versão): agora sim
    # precisa da fonte local (fixture demo ou raw imutável real já
    # persistido pelo CC-201) — nunca coleta nova, só re-leitura;
    # LocalRawMatchSource é estruturalmente incapaz de emitir rede. Frames
    # não são persistidos pelo CC-201 (só eventos), então
    # esta é a única forma de obtê-los; raw_hash é conferido contra o já
    # persistido — nunca mistura uma releitura divergente (fonte
    # trocada/corrompida) com os eventos já persistidos da mesma partida.
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

    enabled_rules = rules.enabled_for(match.patch)
    provenance = rules.provenance_for(match.patch)
    if RULE_R4W in enabled_rules:
        # R4w: pré-passo puro (reatribui as compras do World Atlas do
        # participante 0 aos Apoios; falha segura).
        events = attribute_system_purchases(
            events,
            [p.participant_id for p in source_result.participants if p.role == SUPPORT_ROLE],
        )

    all_decision_states: list[DecisionState] = []
    for participant_id in participant_ids:
        purchases = reconstruct_participant_purchases(
            participant_id=participant_id,
            events=events,
            origin=source_result.origin,
            frames=source_result.frames,
            catalog_id=catalog_id,
            catalog_items=catalog_items,
            excluded_item_ids=excluded_item_ids,
            enabled_rules=enabled_rules,
            system_item_ids=rules.system_item_ids if RULE_R4S in enabled_rules else frozenset(),
        )
        all_decision_states.extend(
            _decision_state_from_purchase(
                match_id, catalog_id, source_result.origin, purchase, provenance
            )
            for purchase in purchases
        )

    try:
        async with unit_of_work(session_factory) as session:
            repo = timeline_repo_factory(session)
            for state in all_decision_states:
                await repo.add_decision_state(state)
            await repo.add_match_reconstruction(
                match_id, RECONSTRUCTION_ALGORITHM_VERSION, catalog_id
            )
    except IntegrityError as exc:
        raise ConflictError(
            f"reconstrução concorrente de {match_id!r} já em andamento/concluída"
        ) from exc

    return MatchReconstruction(
        match_id=match_id, catalog_id=catalog_id, decision_states=all_decision_states
    )
