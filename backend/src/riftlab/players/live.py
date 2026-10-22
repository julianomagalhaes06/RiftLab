"""Partida ao vivo por Riot ID (CC-1211), como o "Live Game" do op.gg. Account-V1 dá o PUUID;
Spectator-V5 (BR1) dá a partida em andamento, que só existe depois da tela de carregamento, com
os dez jogadores, campeões, feitiços, runas principais e banimentos; League-V4 dá o elo de cada
jogador na fila da partida. Não há placar: a API ao vivo da Riot não traz abates, ouro nem itens.

Nada é gravado, nem no banco nem em disco: o resultado fica alguns segundos em memória para o
botão "Verificar de novo" não gastar requisições à toa. O PUUID nunca sai daqui; o Riot ID dos
participantes é o que o próprio jogo mostra na tela de carregamento.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from riftlab.champions.application import latest_champion_assets
from riftlab.config import Settings
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.players.application import (
    _LIMITER,
    SessionFactory,
    _data_dragon_version,
    _key,
    normalize_riot_id,
)
from riftlab.ranked.domain import FLEX_QUEUE, SOLO_QUEUE, RankEntry, parse_league_entries
from riftlab.shared.db import read_only_unit_of_work
from riftlab.shared.errors import (
    PlayerLookupError,
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
)

# Partida consultada há menos disso volta da memória (a composição não muda durante o jogo).
LIVE_CACHE_FOR = timedelta(seconds=20)
# Conta + partida + elo dos dez jogadores.
LIVE_MAX_REQUESTS = 12
FLEX_QUEUE_ID = 440
_CACHE: dict[str, tuple[datetime, dict[str, Any]]] = {}


def _transport(settings: Settings) -> RiotHttpTransport:
    assert settings.riot_api_key is not None
    return RiotHttpTransport(
        settings.riot_api_key,
        RiotFetchBudget(
            max_matches=0, max_requests=LIVE_MAX_REQUESTS, max_total_seconds=20.0, stop_on_429=True
        ),
        rate_limiter=_LIMITER,
    )


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _riot_id(value: Any) -> tuple[str | None, str | None]:
    """ "Nome#TAG" da Spectator-V5; sem `#` (ou ausente), o jogador fica sem Riot ID."""
    if not isinstance(value, str) or "#" not in value:
        return None, None
    name, _, tag = value.rpartition("#")
    return (name or None), (tag or None)


def _bans(raw: Any, team: int) -> list[int]:
    entries = [b for b in raw if isinstance(b, dict)] if isinstance(raw, list) else []
    picked = sorted(
        (b for b in entries if b.get("teamId") == team), key=lambda b: _int(b.get("pickTurn")) or 0
    )
    return [c for b in picked if (c := _int(b.get("championId"))) is not None and c > 0]


def _asset(asset: Any) -> dict[str, str] | None:
    if asset is None:
        return None
    return {
        "name": asset.name,
        "dataDragonVersion": asset.data_dragon_version,
        "imageFile": asset.image_file,
    }


async def live_game(
    factory: SessionFactory,
    settings: Settings,
    game_name: str,
    tag_line: str,
    *,
    transport: RiotHttpTransport | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Partida em andamento do Riot ID: `state` IN_GAME com a partida, ou NOT_IN_GAME.
    Erros da conta ou da Riot saem como PlayerLookupError, como na busca."""
    name, tag = normalize_riot_id(game_name, tag_line)
    key = _key(settings, name, tag)
    now = now or datetime.now(UTC)
    cached = _CACHE.get(key)
    if cached is not None and now - cached[0] < LIVE_CACHE_FOR:
        return cached[1]
    if not settings.riot_api_key:
        raise PlayerLookupError("RIOT_KEY_MISSING")

    own = transport is None
    client = transport or _transport(settings)
    # PUUID → elo na fila da partida (None: sem classificação); ausente: elo não consultado.
    ranks: dict[str, RankEntry | None] = {}
    try:
        try:
            puuid, canonical_name, canonical_tag = await client.resolve_account(name, tag)
        except SourceNotFoundError as exc:
            raise PlayerLookupError("PLAYER_NOT_FOUND") from exc
        try:
            game = await client.fetch_active_game_by_puuid(puuid)
        except SourceNotFoundError:
            game = None
        if game is not None:
            queue_id = _int(game.get("gameQueueConfigId"))
            rank_queue = FLEX_QUEUE if queue_id == FLEX_QUEUE_ID else SOLO_QUEUE
            for raw in game["participants"]:
                other = raw.get("puuid") if isinstance(raw, dict) else None
                if not isinstance(other, str) or not other or raw.get("bot") is True:
                    continue
                try:
                    entries = parse_league_entries(
                        await client.fetch_league_entries_by_puuid(other), other
                    )
                except SourceRateLimitedError:
                    break  # o resto fica sem elo; a partida aparece mesmo assim
                except SourceError:
                    continue
                ranks[other] = entries.get(rank_queue)
    except SourceAuthError as exc:
        raise PlayerLookupError("RIOT_AUTH_FAILED") from exc
    except SourceRateLimitedError as exc:
        raise PlayerLookupError(
            "RIOT_RATE_LIMITED", retry_after_seconds=exc.retry_after_seconds
        ) from exc
    except SourceError as exc:
        raise PlayerLookupError("UPSTREAM_UNAVAILABLE") from exc
    finally:
        if own:
            await client.aclose()

    view: dict[str, Any] = {
        "state": "IN_GAME" if game is not None else "NOT_IN_GAME",
        "gameName": canonical_name,
        "tagLine": canonical_tag,
        "checkedAt": now,
        "game": None,
    }
    if game is not None:
        view["game"] = await _game_view(factory, game, puuid, ranks)
    _CACHE[key] = (now, view)
    return view


async def _game_view(
    factory: SessionFactory, game: dict[str, Any], puuid: str, ranks: dict[str, RankEntry | None]
) -> dict[str, Any]:
    raw_participants = [p for p in game["participants"] if isinstance(p, dict)]
    bans = {team: _bans(game.get("bannedChampions"), team) for team in (100, 200)}
    champion_ids = [c for p in raw_participants if (c := _int(p.get("championId")))]
    champion_ids += [c for team in bans.values() for c in team]
    async with read_only_unit_of_work(factory) as session:
        assets = await latest_champion_assets(session, champion_ids)
        version = await _data_dragon_version(session)
    teams: dict[int, list[dict[str, Any]]] = {100: [], 200: []}
    for raw in raw_participants:
        team = _int(raw.get("teamId"))
        champion_id = _int(raw.get("championId"))
        if team not in teams or champion_id is None:
            continue
        other = raw.get("puuid")
        raw_perks = raw.get("perks")
        perks: dict[str, Any] = raw_perks if isinstance(raw_perks, dict) else {}
        raw_ids = perks.get("perkIds")
        perk_ids: list[Any] = raw_ids if isinstance(raw_ids, list) else []
        known = isinstance(other, str) and other in ranks
        rank = ranks.get(other) if isinstance(other, str) else None
        game_name, tag_line = _riot_id(raw.get("riotId"))
        asset = assets.get(champion_id)
        teams[team].append(
            {
                "gameName": game_name,
                "tagLine": tag_line,
                "bot": raw.get("bot") is True,
                "requested": isinstance(other, str) and other == puuid,
                "championId": champion_id,
                "champion": _asset(asset),
                "spell1Id": _int(raw.get("spell1Id")),
                "spell2Id": _int(raw.get("spell2Id")),
                "keystoneId": _int(perk_ids[0]) if perk_ids else None,
                "perkStyle": _int(perks.get("perkStyle")),
                "perkSubStyle": _int(perks.get("perkSubStyle")),
                "rankState": "UNAVAILABLE" if not known else "READY" if rank else "UNRANKED",
                "rank": (
                    {
                        "tier": rank.tier,
                        "division": rank.division,
                        "leaguePoints": rank.league_points,
                        "wins": rank.wins,
                        "losses": rank.losses,
                    }
                    if rank
                    else None
                ),
            }
        )
    started = _int(game.get("gameStartTime"))
    queue_id = _int(game.get("gameQueueConfigId"))
    return {
        "queueId": queue_id,
        "rankQueue": FLEX_QUEUE if queue_id == FLEX_QUEUE_ID else SOLO_QUEUE,
        "gameMode": game.get("gameMode") if isinstance(game.get("gameMode"), str) else None,
        "mapId": _int(game.get("mapId")),
        # Antes do fim do carregamento a Riot devolve início 0 e duração ≤ 0.
        "startedAt": datetime.fromtimestamp(started / 1000, UTC) if started else None,
        "lengthSeconds": max(_int(game.get("gameLength")) or 0, 0),
        "dataDragonVersion": version,
        "teams": [
            {
                "teamId": team,
                "bans": [{"championId": c, "champion": _asset(assets.get(c))} for c in bans[team]],
                "participants": rows,
            }
            for team, rows in teams.items()
        ],
    }
