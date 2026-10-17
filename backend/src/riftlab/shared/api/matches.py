"""GET /api/v1/matches, GET /api/v1/matches/{id}, GET .../decisions,
GET /api/v1/decisions/{id} (docs/contracts.md). As rotas de DECISÕES (CC-204) e de
PARTIDAS (CC-205: lista, detalhe e preparação) estão montadas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from riftlab.shared.api.common import CamelModel, MatchId
from riftlab.shared.api.enums import BudgetSource, PreparationStatus

STALE_FRAME_THRESHOLD_MS = 90_000


class MatchChampion(CamelModel):
    """Campeão de qualquer participante da partida; não identifica conta própria."""

    champion_id: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)


class ChampionAsset(CamelModel):
    """Nome e retrato oficiais (CC-208), resolvidos pelo `key` numérico no catálogo do
    Data Dragon da versão FIXADA para o patch. O retrato é
    `https://ddragon.leagueoflegends.com/cdn/{dataDragonVersion}/img/champion/{imageFile}`;
    os dois campos são validados por padrão estrito (nunca caminho ou URL livre)."""

    name: str = Field(min_length=1, max_length=100)
    data_dragon_version: str = Field(pattern=r"^\d{1,3}\.\d{1,3}\.\d{1,3}$")
    image_file: str = Field(pattern=r"^[A-Za-z0-9]{1,64}\.png$")


class MatchParticipant(CamelModel):
    """docs/domain-model.md: participants tem subject_hash isolado dos dados
    publicos — nunca exposto aqui. A codificacao exata de "team" (ex.: 100/200
    estilo Riot vs 1/2 simplificado) ainda nao foi decidida (CC-201/202); o
    contrato so garante inteiro nao negativo nesta etapa."""

    participant_id: int = Field(ge=1, le=10)
    champion_id: int = Field(ge=1)
    team: int = Field(ge=0)
    role: str | None = Field(default=None, min_length=1, max_length=20)
    win: bool | None = Field(
        default=None, strict=True, description="Vitória deste participante; null se não observada."
    )
    kills: int | None = Field(default=None, strict=True, ge=0, le=2_147_483_647)
    deaths: int | None = Field(default=None, strict=True, ge=0, le=2_147_483_647)
    assists: int | None = Field(default=None, strict=True, ge=0, le=2_147_483_647)
    total_minions_killed: int | None = Field(default=None, strict=True, ge=0, le=2_147_483_647)
    neutral_minions_killed: int | None = Field(default=None, strict=True, ge=0, le=2_147_483_647)
    champion: ChampionAsset | None = Field(
        default=None,
        description="null em partida DEMO, patch sem mapping, catálogo ainda não "
        "sincronizado ou campeão ausente do catálogo: a interface mostra o id numérico.",
    )


class MatchSummary(CamelModel):
    """Item de GET /matches: sem PUUID, Riot ID ou hash de jogador.
    `champions_present` abrange qualquer participante. `local_participant_id` só
    aponta a posição da conta local configurada (mesma regra do Painel) e é null
    quando ela não é conhecida ou não está na partida."""

    match_id: MatchId
    queue: int = Field(ge=0)
    map: int = Field(ge=0)
    patch: str = Field(min_length=1, max_length=20)
    start_at: datetime
    duration_seconds: int = Field(ge=0)
    preparation_status: PreparationStatus
    champions_present: list[MatchChampion] = Field(default_factory=list)
    support_level: Literal["LIMITED", "DEMO"] = Field(
        description="Sem avaliação de ranking neste patch: suporte limitado; DEMO é ilustrativo."
    )
    support_reason: Literal["PATCH_NOT_VALIDATED", "DEMO_DATA"]
    participants: list[MatchParticipant] = Field(
        default_factory=list,
        description="Fatos pós-partida dos participantes, por participantId; sem identidade.",
    )
    local_participant_id: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="Participante da conta local configurada (DEMO: 1); null se desconhecido.",
    )


class MatchPreparation(CamelModel):
    """Estado da preparação da partida (catálogo -> reconstrução -> extração de
    decisões). `error_code` só existe em FAILED (falha final) e em PENDING
    aguardando nova tentativa (falha transitória); `retry_at` só em PENDING
    aguardando nova tentativa. READY = decisões extraídas."""

    status: PreparationStatus
    error_code: str | None = Field(default=None, max_length=64)
    retry_at: datetime | None = None
    attempts: int = Field(default=0, ge=0)


class MatchDetail(MatchSummary):
    """GET /matches/{id}: patch, queue, map, participantes (sem identificador
    pessoal) e estado de preparação; 404 desconhecido."""

    participants: list[MatchParticipant]
    preparation: MatchPreparation


class DecisionReason(CamelModel):
    """Motivo de inelegibilidade. `code` no vocabulário do contrato
    (MISSING_CATALOG, OUT_OF_SCOPE, INVENTORY_UNCERTAIN) mais o neutro
    TARGET_NOT_ELIGIBLE; `sub_reasons` preserva literalmente os códigos de
    origem. Nunca revela a classe do item comprado nem que a compra foi
    desfeita (modo Estudo: antes de revelar, só informação pré-decisão)."""

    code: str = Field(min_length=1, max_length=64)
    sub_reasons: list[str] = Field(default_factory=list)


class DecisionStateSummary(CamelModel):
    """Item de GET /matches/{id}/decisions (CC-204): SÓ informação pré-decisão.
    `decision_id` é o id do PONTO de decisão. Proibido aqui: item comprado,
    classe do alvo, bloco retrospectivo, features, qualidade cumulativa e
    subject_hash."""

    decision_id: UUID
    participant_id: int = Field(ge=1, le=10)
    event_source_index: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)
    eligible: bool
    reason_codes: list[str] = Field(default_factory=list)
    main_item_ordinal: int | None = Field(default=None, ge=1)
    budget_source: BudgetSource
    frame_age_ms: int | None = Field(default=None, ge=0)
    stale_frame: bool

    @model_validator(mode="after")
    def _stale_frame_consistent_with_age(self) -> DecisionStateSummary:
        _check_stale_consistent(self.frame_age_ms, self.stale_frame)
        return self


class InventoryItem(CamelModel):
    """`name` vem do catálogo FIXADO pelas versões do ponto (linha imutável),
    nunca do canônico atual; null quando não há catálogo ou o item não consta
    nele (a interface mostra o ID com rótulo honesto). Nunca revela o alvo."""

    item_id: int = Field(ge=1)
    quantity: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=200)


class DecisionInventory(CamelModel):
    """Inventário ANTES da decisão como multiconjunto (nunca um conjunto de ids
    soltos: dois itens iguais são quantity=2). `trinket_item_id` é uma slot
    separada; null = slot vazia (estado conhecido)."""

    items: list[InventoryItem] = Field(default_factory=list)
    trinket_item_id: int | None = Field(default=None, ge=1)


class DecisionBudget(CamelModel):
    """Orçamento imediato, separado da elegibilidade. `value` é null quando
    desconhecido (nunca 0). STALE_FRAME/UNKNOWN_BUDGET em `codes` impedem
    afirmar compra imediata, mas não tornam a decisão inelegível."""

    value: int | None = Field(default=None, ge=0)
    source: BudgetSource
    frame_age_ms: int | None = Field(default=None, ge=0)
    stale_frame: bool
    codes: list[str] = Field(default_factory=list)
    immediate_purchase_assertable: bool

    @model_validator(mode="after")
    def _stale_frame_consistent_with_age(self) -> DecisionBudget:
        _check_stale_consistent(self.frame_age_ms, self.stale_frame)
        return self


class ObservedLevel(CamelModel):
    """Nível do mesmo frame estritamente anterior usado para o ouro; ausência
    explícita (`value` null com `absent_reason`), nunca 0."""

    value: int | None = Field(default=None, ge=1)
    frame_age_ms: int | None = Field(default=None, ge=0)
    absent_reason: str | None = Field(default=None, max_length=64)


class DecisionVersionInfo(CamelModel):
    """Todas as versões que produziram o ponto (linhas imutáveis: uma extração
    antiga continua consultável com as suas versões)."""

    data_dragon_version: str | None = None
    reconstruction_version: int = Field(ge=1)
    selection_version: str
    feature_schema_version: str
    scope_version: str
    catalog_id: UUID | None = None
    curation_rules_version: str | None = None
    classification_version: str | None = None


class DecisionState(CamelModel):
    """GET /decisions/{id} (CC-204): "snapshot, inventário, fonte de orçamento,
    elegibilidade" — SÓ informação pré-decisão (ver DecisionStateSummary)."""

    decision_id: UUID
    match_id: MatchId
    participant_id: int = Field(ge=1, le=10)
    event_source_index: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)
    eligible: bool
    main_item_ordinal: int | None = Field(default=None, ge=1)
    reasons: list[DecisionReason] = Field(default_factory=list)
    inventory: DecisionInventory
    budget: DecisionBudget
    level: ObservedLevel
    versions: DecisionVersionInfo


def _check_stale_consistent(frame_age_ms: int | None, stale_frame: bool) -> None:
    if frame_age_ms is not None and frame_age_ms > STALE_FRAME_THRESHOLD_MS and not stale_frame:
        raise ValueError("frameAgeMs > 90000ms deve marcar staleFrame=true (docs/domain-model.md)")


class TimelineItemEvent(CamelModel):
    """Compra ou venda de item; desfazer já aplicado. `item_class` vem da
    classificação do catálogo fixado pela reconstrução (null sem catálogo)."""

    timestamp_ms: int = Field(ge=0)
    item_id: int = Field(ge=1)
    kind: Literal["PURCHASE", "SOLD"]
    item_class: str | None = Field(default=None, max_length=32)


class TimelineSkillUp(CamelModel):
    timestamp_ms: int = Field(ge=0)
    skill_slot: int = Field(ge=1, le=4, description="1=Q, 2=W, 3=E, 4=R.")
    level_up_type: str = Field(max_length=32)


class ParticipantTimeline(CamelModel):
    participant_id: int = Field(ge=1, le=10)
    items: list[TimelineItemEvent] = Field(default_factory=list)
    skills: list[TimelineSkillUp] = Field(default_factory=list)


class TimelineKill(CamelModel):
    timestamp_ms: int = Field(ge=0)
    killer_id: int = Field(ge=0, le=10, description="0 = executado (torre, monstro).")
    victim_id: int = Field(ge=1, le=10)
    assisting_ids: list[int] = Field(default_factory=list)


class TimelineObjective(CamelModel):
    timestamp_ms: int = Field(ge=0)
    kind: Literal["DRAGON", "BARON", "HERALD", "VOIDGRUB", "ATAKHAN", "TOWER", "INHIBITOR", "OTHER"]
    team: int | None = Field(default=None, description="Time que conquistou o objetivo.")
    sub_type: str | None = Field(default=None, max_length=40)


class TimelineGoldFrame(CamelModel):
    timestamp_ms: int = Field(ge=0)
    gold: list[int | None] = Field(
        description="Ouro total por participante, índice 0 = participante 1; null ausente."
    )


class MatchTimeline(CamelModel):
    """GET /matches/{id}/timeline: fatos da partida para a tela de análise. `gold_frames`
    é null enquanto a preparação não gravou os quadros de ouro."""

    match_id: MatchId
    data_dragon_version: str | None = Field(default=None, pattern=r"^\d{1,3}\.\d{1,3}\.\d{1,3}$")
    participants: list[ParticipantTimeline]
    kills: list[TimelineKill]
    objectives: list[TimelineObjective]
    gold_frames: list[TimelineGoldFrame] | None = None
