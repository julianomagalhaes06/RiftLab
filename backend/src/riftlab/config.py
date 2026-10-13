from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuracao tipada da aplicacao. Nenhum valor exige segredo real para subir
    em modo demo: ausencia de RIFTLAB_RIOT_API_KEY nao impede live/ready nem a tela
    inicial do frontend."""

    model_config = SettingsConfigDict(env_prefix="RIFTLAB_", extra="ignore", env_ignore_empty=True)

    environment: str = "development"
    log_level: str = "INFO"

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    database_url: str = "postgresql+asyncpg://riftlab:riftlab@db:5432/riftlab"
    database_ready_timeout_seconds: float = 2.0

    riot_api_key: str | None = None

    # HMAC (nunca hash simples/chave fixa) de PUUID em modo RIOT (CC-201): só
    # exigido quando um job RIOT de verdade precisa persistir participantes —
    # DEMO nunca depende disso (fixtures não tem PUUID real algum).
    subject_hash_secret: str | None = None

    # CC-310: arquivo local montado explicitamente na API, fora da imagem/Git.
    # No checkout host, configurar RIFTLAB_RANKED_ACCOUNT_FILE para o caminho local.
    ranked_account_file: str = "/app/.private/riot-account.json"

    # Caminho absoluto por padrao (analogo a database_url, que ja assume a
    # rede do compose): corresponde ao WORKDIR /app da imagem runtime
    # (backend/Dockerfile). Para dev fora de container, sobrescrever via env
    # var — nunca calculado via Path(__file__).parents[N], que teria
    # profundidade diferente dentro do container (COPY backend/src ./src
    # remove o nivel "backend/" que existe no checkout do host).
    raw_storage_dir: str = "/app/data/raw/imports"

    # Mesmo racional de raw_storage_dir (absoluto, não Path(__file__).parents[N]):
    # o Dockerfile copia configs/catalog-mappings.yaml para este caminho, tanto
    # na imagem runtime (worker/api) quanto na imagem test (CC-202).
    catalog_mappings_path: str = "/app/configs/catalog-mappings.yaml"

    # Recorte real (configs/scope.yaml, CC-105), lido pela extração de decisões
    # de origem REAL (CC-204). Mesmo racional: caminho absoluto copiado pelo
    # Dockerfile; DEMO nunca lê este arquivo.
    scope_path: str = "/app/configs/scope.yaml"

    # Registro de regras EMPÍRICAS do reducer (CC-207): estado por regra e por patch,
    # com proveniência. Nasce tudo PENDING; só liga por patch com evidência confirmatória.
    # Mesmo racional: caminho absoluto copiado pelo Dockerfile.
    empirical_rules_path: str = "/app/configs/empirical-rules.yaml"

    # CC-504: interpretador de objetivo. "rules" (padrão, sem modelo) ou "local_model"
    # (sidecar em loopback, ml/objectives serve). Timeout abre o formulário (RNF03).
    objective_parser: str = "rules"
    objective_model_url: str | None = None
    objective_model_version: str = "local_model_unversioned"
    objective_parser_timeout_seconds: float = 15.0

    # CC-603: retenção (cc retention). Só lista por padrão; apagar exige --execute.
    match_retention_days: int = 90
    rank_retention_days: int = 365

    worker_heartbeat_path: str = "/tmp/riftlab-worker.heartbeat"
    # Toque do heartbeat: a cada ciclo do worker (reserva -> busca -> persiste
    # um job), e a cada espera deste intervalo quando ocioso — substitui o
    # intervalo fixo do CC-101 (worker_heartbeat_interval_seconds), agora sem
    # uso: processar um job já toca o heartbeat, não é preciso um timer
    # separado (CC-201).
    worker_poll_interval_seconds: float = 2.0

    @property
    def demo_mode(self) -> bool:
        """Verdadeiro quando nao ha chave Riot configurada; o servidor continua
        saudavel e a importacao real fica indisponivel ate CC-105/201."""
        return self.riot_api_key is None


@lru_cache
def get_settings() -> Settings:
    return Settings()
