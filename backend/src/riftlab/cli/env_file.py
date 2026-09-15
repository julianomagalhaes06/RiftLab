from __future__ import annotations

from pathlib import Path

from riftlab.config import Settings

# backend/src/riftlab/cli/env_file.py -> parents[4] é a raiz do repositório
# (cli -> riftlab -> src -> backend -> raiz). Fixo pelo layout do pacote, não
# pelo cwd de quem invoca `cc` (uv run a partir de backend/, ou WORKDIR /app
# no container) — nunca pega .env errado por acidente de diretório corrente.
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"


def resolve_env_file(explicit: Path | None) -> Path:
    return explicit if explicit is not None else DEFAULT_ENV_FILE


def load_settings(env_file: Path | None = None) -> Settings:
    """Carrega Settings lendo o .env indicado (ou o padrão da raiz do repo) via
    pydantic-settings/python-dotenv — nunca shell-source, nunca executa o
    arquivo. Variável de ambiente real do processo sempre vence o valor do
    .env (precedência padrão do pydantic-settings). Arquivo ausente não é
    erro: Settings cai para variáveis de ambiente/defaults, preservando o modo
    demo. Nunca imprime o conteúdo do arquivo nem os valores carregados."""
    resolved = resolve_env_file(env_file)
    return Settings(_env_file=resolved)  # type: ignore[call-arg]
