from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuracao tipada da aplicacao. Nenhum valor exige segredo real para subir
    em modo demo: ausencia de RIFTLAB_RIOT_API_KEY nao impede live/ready nem a tela
    inicial do frontend."""

    model_config = SettingsConfigDict(env_prefix="RIFTLAB_", extra="ignore", env_ignore_empty=True)

    environment: str = "development"
    log_level: str = "INFO"

    # Loopback por padrão (CC-1215): a API não tem autenticação. O container define
    # RIFTLAB_API_HOST=0.0.0.0 e o compose publica a porta só em 127.0.0.1.
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    # Nomes de Host aceitos pela API (sem porta), contra DNS rebinding e acesso pela rede local
    # (CC-1215). O compose acrescenta `api`, o nome com que o proxy do frontend chega.
    api_allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")

    # Contas locais (Sprint 2, cards #10/#11): com auth_required, as rotas /api/v1 exigem sessão
    # (cookie HttpOnly, SameSite=Strict) e o cabeçalho X-RiftLab-Request nas alterações.
    auth_required: bool = False
    auth_session_days: int = 7
    # Só com HTTPS: o compose local serve HTTP em loopback.
    auth_cookie_secure: bool = False
    # E-mails da conta (código de confirmação e de recuperação de senha). O compose aponta para o
    # Mailpit local; para enviar de verdade pelo Gmail, ver `.env.example`.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = False
    smtp_from: str = "RiftLab <no-reply@riftlab.local>"
    smtp_timeout_seconds: float = 10.0

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

    # F1 (CC-1003/CC-1004): referência da revisão e modelo de chance de vitória, ambos
    # agregados e versionados em configs/match-review (ausentes ⇒ a revisão sai sem eles).
    match_review_reference_path: str = "/app/configs/match-review/reference-16.19-solo-v1.json"
    win_probability_model_path: str = "/app/configs/match-review/win-probability-16.19-v1.json"

    # CC-504: interpretador de objetivo. "rules" (padrão, sem modelo) ou "local_model"
    # (sidecar em loopback, ml/objectives serve). Timeout abre o formulário (RNF03).
    objective_parser: str = "rules"
    objective_model_url: str | None = None
    objective_model_socket_path: str | None = None
    objective_model_version: str = "local_model_unversioned"
    objective_parser_timeout_seconds: float = 15.0

    # CC-603: retenção (cc retention). Só lista por padrão; apagar exige --execute.
    match_retention_days: int = 90
    # CC-605: na demonstração (sem chave da Riot), Minha posição lê um elo FICTÍCIO marcado
    # como tal, gravado por `cc ranked demo-seed`; nunca consulta a Riot.
    demo_ranked: bool = False
    rank_retention_days: int = 365
    # CC-1103: perfis de terceiros buscados por Riot ID (com o elo e as execuções de
    # sincronização) saem depois disso sem nova consulta. A conta configurada não expira.
    player_profile_retention_days: int = 7

    worker_heartbeat_path: str = "/tmp/riftlab-worker.heartbeat"
    # Toque do heartbeat: a cada ciclo do worker (reserva -> busca -> persiste
    # um job), e a cada espera deste intervalo quando ocioso — substitui o
    # intervalo fixo do CC-101 (worker_heartbeat_interval_seconds), agora sem
    # uso: processar um job já toca o heartbeat, não é preciso um timer
    # separado (CC-201).
    worker_poll_interval_seconds: float = 2.0
    # Importações simultâneas no worker: cada uma espera a Riot (partida + timeline) quase o
    # tempo todo, então várias em paralelo trazem um histórico de 20 partidas em segundos, em
    # vez de uma a uma. As chamadas dividem um limitador da Riot no processo (janelas da chave).
    worker_import_concurrency: int = Field(default=4, ge=1, le=16)
    # Patch atual do jogo (versions.json público): renovado pelo worker só no modo real.
    published_patch_refresh_seconds: float = 6 * 3600.0
    # CC-901: sincronização do histórico da conta própria ao abrir a aba Histórico.
    # Intervalo mínimo entre execuções, espera maior depois de chave rejeitada,
    # janela da primeira listagem e teto de IDs por listagem (uma requisição).
    history_sync_cooldown_seconds: float = 300.0
    history_sync_auth_cooldown_seconds: float = 3600.0
    history_sync_lookback_days: int = 14
    history_sync_max_ids: int = 20

    @property
    def demo_mode(self) -> bool:
        """Verdadeiro quando nao ha chave Riot configurada; o servidor continua
        saudavel e a importacao real fica indisponivel ate CC-105/201."""
        return self.riot_api_key is None


@lru_cache
def get_settings() -> Settings:
    return Settings()
