from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any, Literal

from riftlab.ingestion.infrastructure.riot_account import RiotAccountConfig
from riftlab.shared.errors import SourceSchemaError

RankedQueue = Literal["RANKED_SOLO_5x5", "RANKED_FLEX_SR"]
SOLO_QUEUE: RankedQueue = "RANKED_SOLO_5x5"
FLEX_QUEUE: RankedQueue = "RANKED_FLEX_SR"
QUEUES = (SOLO_QUEUE, FLEX_QUEUE)
DIVISIONS = ("IV", "III", "II", "I")
DIVIDED_TIERS = ("IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND")
APEX_TIERS = ("MASTER", "GRANDMASTER", "CHALLENGER")
TIERS = DIVIDED_TIERS + APEX_TIERS
SEASON_PATTERN = re.compile(r"^[0-9]{4}(?:-[A-Za-z0-9]{1,12})?$")


@dataclass(frozen=True, slots=True)
class RankEntry:
    queue: str
    tier: str
    division: str | None
    league_points: int
    wins: int
    losses: int


def account_key(account: RiotAccountConfig, secret: str | None) -> str | None:
    """Chave local para seleção do GET; renomear Riot ID oculta histórico anterior."""
    if not secret:
        return None
    identity = (
        f"ranked-account-v1\0BR1\0{account.game_name.casefold()}\0{account.tag_line.casefold()}"
    )
    return hmac.new(secret.encode(), identity.encode(), hashlib.sha256).hexdigest()


def parse_league_entries(raw: list[Any], puuid: str) -> dict[str, RankEntry]:
    """Aceita somente entradas da conta resolvida; array vazio = não classificado."""
    parsed: dict[str, RankEntry] = {}
    for value in raw:
        if not isinstance(value, dict):
            raise SourceSchemaError(code="SCHEMA_UNSUPPORTED", detail="entrada de league inválida")
        queue = value.get("queueType")
        if not isinstance(queue, str) or not queue or value.get("puuid") != puuid:
            raise SourceSchemaError(
                code="SCHEMA_UNSUPPORTED", detail="identidade/fila de league inválida"
            )
        if queue not in QUEUES:
            continue
        if queue in parsed:
            raise SourceSchemaError(
                code="SCHEMA_UNSUPPORTED", detail="identidade/fila de league inválida"
            )
        tier, division = value.get("tier"), value.get("rank")
        lp, wins, losses = value.get("leaguePoints"), value.get("wins"), value.get("losses")
        if (
            tier not in TIERS
            or (tier in DIVIDED_TIERS and division not in DIVISIONS)
            or not all(
                isinstance(number, int) and not isinstance(number, bool) and number >= 0
                for number in (lp, wins, losses)
            )
        ):
            raise SourceSchemaError(code="SCHEMA_UNSUPPORTED", detail="campos de league inválidos")
        assert isinstance(lp, int) and isinstance(wins, int) and isinstance(losses, int)
        if tier in DIVIDED_TIERS and lp > 100:
            raise SourceSchemaError(code="SCHEMA_UNSUPPORTED", detail="PDL de league inválido")
        parsed[queue] = RankEntry(
            queue=queue,
            tier=tier,
            division=division if tier in DIVIDED_TIERS else None,
            league_points=lp,
            wins=wins,
            losses=losses,
        )
    return parsed
