"""Conta Riot "própria" de uma requisição (Sprint 2, card #13).

- Sem usuário na sessão (instalação de uma pessoa só, login não exigido): a conta do arquivo
  configurado (`ranked_account_file`), como antes das contas locais. Representada por
  `CONFIGURED`, que as funções resolvem quando precisam.
- Com usuário: a conta Riot vinculada a ele pelo desafio do ícone, ou `None` enquanto não
  houver vínculo; nunca cai para a conta de outra pessoa.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from riftlab.config import Settings
from riftlab.ingestion.infrastructure.riot_account import (
    RiotAccountConfig,
    RiotAccountConfigError,
    load_riot_account,
)


class _Configured:
    def __repr__(self) -> str:
        return "CONFIGURED"


CONFIGURED: Final = _Configured()
OwnAccount = RiotAccountConfig | _Configured | None
# Motivo para telas da conta quando o usuário logado ainda não vinculou uma conta Riot.
NOT_LINKED = "ACCOUNT_NOT_LINKED"


def configured_account(settings: Settings) -> RiotAccountConfig | None:
    try:
        return load_riot_account(Path(settings.ranked_account_file))
    except (RiotAccountConfigError, OSError):
        return None


def resolve(settings: Settings, account: OwnAccount) -> RiotAccountConfig | None:
    return configured_account(settings) if isinstance(account, _Configured) else account
