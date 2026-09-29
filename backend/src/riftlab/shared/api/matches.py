"""GET /api/v1/matches, GET /api/v1/matches/{id}, GET .../decisions,
GET /api/v1/decisions/{id} (docs/contracts.md). As rotas de DECISÕES (CC-204) e de
PARTIDAS (CC-205: lista, detalhe e preparação) estão montadas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from riftlab.shared.api.common import CamelModel, MatchId
from riftlab.shared.api.enums import BudgetSource, PreparationStatus

STALE_FRAME_THRESHOLD_MS = 90_000


class MatchChampion(CamelModel):
    """Campeão de qualquer participante da partida; não identifica conta própria."""

    champion_id: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)


class MatchSummary(CamelModel):
    """Item de GET /matches: sem identificador de conta própria ou PUUID.
    `champions_present` abrange qualquer participante, nunca o jogador local."""

    match_id: MatchId
    queue: int = Field(ge=0)
    map: int = Field(ge=0)
    patch: str = Field(min_length=1, max_length=20)
    start_at: datetime
    duration_seconds: int = Field(ge=0)
    preparation_status: PreparationStatus
    champions_present: list[MatchChampion] = Field(default_factory=list)


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
    champion: ChampionAsset | None = Field(
        default=None,
        description="null em partida DEMO, patch sem mapping, catálogo ainda não "
        "sincronizado ou campeão ausente do catálogo: a interface mostra o id numérico.",
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
