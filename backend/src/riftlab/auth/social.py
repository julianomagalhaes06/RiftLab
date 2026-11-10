"""Login com Google e Discord: OAuth 2.0 com troca do código no servidor.

Cada provedor só fica ativo com id e segredo configurados. A rota de início grava um `state`
aleatório num cookie HttpOnly de 10 minutos, e a volta só vale com o mesmo valor: isso impede que
outro site faça a vítima entrar na conta dele. O cookie é SameSite=Lax de propósito, porque a
volta é uma navegação vinda do provedor e um cookie Strict não seria enviado.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args
from urllib.parse import urlencode

import httpx

from riftlab.auth.application import NAME_MAX, AuthError, clean_name
from riftlab.config import Settings

SocialName = Literal["google", "discord"]
NEXT_MAX = 512
_TIMEOUT = httpx.Timeout(10.0)


@dataclass(frozen=True, slots=True)
class Profile:
    subject: str
    email: str | None
    email_verified: bool
    name: str | None


@dataclass(frozen=True, slots=True)
class Provider:
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str
    prompt: str
    parse: Callable[[dict[str, Any]], Profile]


def _parse_google(data: dict[str, Any]) -> Profile:
    return Profile(
        subject=str(data["sub"]),
        email=data.get("email"),
        email_verified=data.get("email_verified") in (True, "true"),
        name=data.get("name"),
    )


def _parse_discord(data: dict[str, Any]) -> Profile:
    return Profile(
        subject=str(data["id"]),
        email=data.get("email"),
        email_verified=data.get("verified") is True,
        name=data.get("global_name") or data.get("username"),
    )


PROVIDERS: dict[SocialName, Provider] = {
    "google": Provider(
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scope="openid email profile",
        prompt="select_account",
        parse=_parse_google,
    ),
    "discord": Provider(
        authorize_url="https://discord.com/oauth2/authorize",
        token_url="https://discord.com/api/oauth2/token",
        userinfo_url="https://discord.com/api/users/@me",
        scope="identify email",
        prompt="none",
        parse=_parse_discord,
    ),
}


def credentials(settings: Settings, provider: SocialName) -> tuple[str, str] | None:
    client_id, secret = (
        (settings.google_client_id, settings.google_client_secret)
        if provider == "google"
        else (settings.discord_client_id, settings.discord_client_secret)
    )
    return (client_id, secret) if client_id and secret else None


def enabled_providers(settings: Settings) -> list[SocialName]:
    names = cast(tuple[SocialName, ...], get_args(SocialName))
    return [name for name in names if credentials(settings, name) is not None]


def redirect_uri(settings: Settings, provider: SocialName) -> str:
    """O endereço público de volta, que precisa estar cadastrado igualzinho no provedor."""
    return f"{settings.public_url.rstrip('/')}/api/v1/auth/social/{provider}/callback"


def authorize_url(provider: SocialName, client_id: str, state: str, callback: str) -> str:
    config = PROVIDERS[provider]
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": callback,
            "response_type": "code",
            "scope": config.scope,
            "state": state,
            "prompt": config.prompt,
        }
    )
    return f"{config.authorize_url}?{query}"


def safe_next(value: str | None) -> str | None:
    """Caminho interno para voltar depois de entrar; recusa outro site ("//x", "https:")."""
    if (
        not value
        or len(value) > NEXT_MAX
        or not value.startswith("/")
        or value.startswith(("//", "/\\"))
    ):
        return None
    return value


def display_name(raw: str | None) -> str | None:
    """O nome do provedor é só conveniência: fora do formato, a conta fica sem nome."""
    if not raw:
        return None
    try:
        return clean_name(raw[:NAME_MAX])
    except AuthError:
        return None


async def fetch_profile(
    provider: SocialName,
    secrets: tuple[str, str],
    code: str,
    callback: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Profile:
    """Troca o código pelo token e lê o perfil. Erro de rede ou resposta inesperada sobe."""
    config = PROVIDERS[provider]
    client_id, client_secret = secrets
    async with httpx.AsyncClient(transport=transport, timeout=_TIMEOUT) as client:
        token = await client.post(
            config.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Accept": "application/json"},
        )
        token.raise_for_status()
        access_token = token.json()["access_token"]
        info = await client.get(
            config.userinfo_url, headers={"Authorization": f"Bearer {access_token}"}
        )
        info.raise_for_status()
        return config.parse(info.json())
