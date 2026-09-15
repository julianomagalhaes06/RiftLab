from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# backend/src/riftlab/ingestion/infrastructure/riot_account.py -> parents[5] é
# a raiz do repositório (infrastructure -> ingestion -> riftlab -> src ->
# backend -> raiz). Mesma lógica fixa de env_file.py: não depende do cwd de
# quem invoca `cc`.
DEFAULT_ACCOUNT_FILE = Path(__file__).resolve().parents[5] / ".private" / "riot-account.json"

# Recorte fechado do projeto (docs/architecture.md); qualquer outro valor é
# rejeitado explicitamente, nunca coagido/adaptado silenciosamente.
ALLOWED_PLATFORM = "BR1"
ALLOWED_REGIONAL_ROUTING = "AMERICAS"

_REQUIRED_FIELDS = ("gameName", "tagLine", "platform", "regionalRouting")


class RiotAccountConfigError(Exception):
    """Arquivo de conta local ausente, malformado ou fora do recorte suportado
    (BR1/Americas). Nunca ecoa o conteúdo do arquivo nem os valores lidos —
    só nomes de campo e valores esperados/permitidos."""


@dataclass(frozen=True, slots=True)
class RiotAccountConfig:
    """Conta pessoal local (`.private/riot-account.json`, fora do Git). Usada
    só para descobrir as próprias partidas concluídas (CC-105); nunca
    persistida em manifesto/arquivo fora de `.private/`/`data/`."""

    game_name: str
    tag_line: str
    platform: str
    regional_routing: str


def load_riot_account(path: Path) -> RiotAccountConfig:
    if not path.exists():
        raise RiotAccountConfigError(f"arquivo de conta não encontrado: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RiotAccountConfigError(f"arquivo de conta não é JSON válido: {path}") from exc
    except UnicodeDecodeError as exc:
        raise RiotAccountConfigError(f"arquivo de conta não é UTF-8 válido: {path}") from exc

    if not isinstance(raw, dict):
        raise RiotAccountConfigError(f"arquivo de conta deve ser um objeto JSON: {path}")

    missing = sorted(field for field in _REQUIRED_FIELDS if field not in raw)
    if missing:
        raise RiotAccountConfigError(
            f"arquivo de conta sem campo(s) obrigatório(s): {', '.join(missing)}"
        )

    game_name, tag_line, platform, regional_routing = (
        raw["gameName"],
        raw["tagLine"],
        raw["platform"],
        raw["regionalRouting"],
    )

    if not isinstance(game_name, str) or not game_name.strip():
        raise RiotAccountConfigError("gameName deve ser uma string não vazia")
    if not isinstance(tag_line, str) or not tag_line.strip():
        raise RiotAccountConfigError("tagLine deve ser uma string não vazia")
    if not isinstance(platform, str) or platform.strip().upper() != ALLOWED_PLATFORM:
        raise RiotAccountConfigError(
            f"platform fora do recorte suportado pelo projeto (esperado {ALLOWED_PLATFORM!r})"
        )
    if (
        not isinstance(regional_routing, str)
        or regional_routing.strip().upper() != ALLOWED_REGIONAL_ROUTING
    ):
        raise RiotAccountConfigError(
            f"regionalRouting fora do recorte suportado (esperado {ALLOWED_REGIONAL_ROUTING!r})"
        )

    return RiotAccountConfig(
        game_name=game_name.strip(),
        tag_line=tag_line.strip(),
        # normalizados: comparação de platform/regionalRouting já é
        # case-insensitive (bug real encontrado ao rodar contra a conta real
        # configurada pelo usuário — a comparação original era sensível a
        # maiúsculas/minúsculas), então o restante do código sempre recebe a
        # forma canônica em maiúsculas.
        platform=platform.strip().upper(),
        regional_routing=regional_routing.strip().upper(),
    )
