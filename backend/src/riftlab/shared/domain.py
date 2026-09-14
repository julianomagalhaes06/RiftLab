from __future__ import annotations

from enum import StrEnum


class SourceOrigin(StrEnum):
    """Origem de dados retornada por MatchSource/CatalogSource. docs/architecture.md
    exige completed_match ou curated_demo para criar uma decisão; uma origem live
    (partida em andamento) nunca é um valor válido aqui, conforme RF17. Só
    CURATED_DEMO tem adapter nesta etapa (CC-104): COMPLETED_MATCH é declarado sem
    implementação, mesmo padrão de ImportMode.RIOT desde o CC-101, para os dois
    valores válidos existirem no tipo antes do adapter real (CC-105/201) — nenhum
    terceiro valor pode ser inventado por engano."""

    CURATED_DEMO = "curated_demo"
    COMPLETED_MATCH = "completed_match"
