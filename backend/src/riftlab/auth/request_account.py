"""Conta própria da requisição HTTP (card #13): usuário da sessão ou arquivo configurado.

Sem sessão e com login exigido (navegação pública), não há conta própria: a conta do arquivo
configurado é a da instalação de uma pessoa só e não vale para quem visita sem conta."""

from __future__ import annotations

from fastapi import Request

from riftlab.auth.riot_link import linked_account
from riftlab.shared.own_account import CONFIGURED, OwnAccount


async def own_account(request: Request) -> OwnAccount:
    user = getattr(request.state, "user", None)
    if user is None:
        return None if request.app.state.settings.auth_required else CONFIGURED
    return await linked_account(request.app.state.session_factory, user.id)


def not_linked(request: Request, account: OwnAccount) -> bool:
    """Usuário logado sem conta Riot vinculada."""
    return account is None and getattr(request.state, "user", None) is not None
