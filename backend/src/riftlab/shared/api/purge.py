"""CC-603: prévia e pedido de expurgo de uma partida (docs/contracts.md)."""

from __future__ import annotations

from pydantic import Field

from riftlab.shared.api.common import CamelModel, MatchId


class PurgeCounts(CamelModel):
    participants: int = Field(ge=0)
    timeline_events: int = Field(ge=0)
    decision_states: int = Field(ge=0)
    decision_points: int = Field(ge=0)
    analyses: int = Field(ge=0)
    learning_attempts: int = Field(ge=0)
    feedback: int = Field(ge=0)


class PurgeDataset(CamelModel):
    task: str = Field(max_length=60)
    patch: str = Field(max_length=20)
    description: str = Field(max_length=300)
    already_invalidated: bool


class PurgeModel(CamelModel):
    name: str = Field(max_length=80)
    kind: str = Field(max_length=20)
    status: str = Field(max_length=20)


class PurgePreview(CamelModel):
    """O que o expurgo removeria e invalidaria; nada é alterado por esta leitura."""

    match_id: MatchId
    counts: PurgeCounts
    datasets: list[PurgeDataset]
    models: list[PurgeModel]
    unverifiable_datasets: int = Field(ge=0)
    limitations: list[str]
