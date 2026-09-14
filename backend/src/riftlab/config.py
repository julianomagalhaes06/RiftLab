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

    worker_heartbeat_path: str = "/tmp/riftlab-worker.heartbeat"
    worker_heartbeat_interval_seconds: float = 10.0

    @property
    def demo_mode(self) -> bool:
        """Verdadeiro quando nao ha chave Riot configurada; o servidor continua
        saudavel e a importacao real fica indisponivel ate CC-105/201."""
        return self.riot_api_key is None


@lru_cache
def get_settings() -> Settings:
    return Settings()
