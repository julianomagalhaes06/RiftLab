"""Leitura de partidas importadas (CC-205): SÓ banco, em transação READ ONLY.
Nunca importa, prepara, reconstrói, extrai nem usa rede. Nada pessoal sai daqui:
o participante é projetado para `ParticipantView`, que não tem `subject_hash`.

Ordem da lista (estável e documentada): `startAt` decrescente, desempate por
`matchId` decrescente; paginação por keyset sobre essa dupla (uma partida nova
não repete nem desloca as páginas seguintes de um cursor em andamento)."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping
from riftlab.champions.application import ChampionAsset, champion_assets
from riftlab.decisions.domain import ParticipantView
from riftlab.ingestion.domain import Match
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.preparation.application import PreparationInfo, preparation_info
from riftlab.shared.api.common import MATCH_ID_DEMO_PATTERN, MATCH_ID_REAL_PATTERN
from riftlab.shared.db import read_only_unit_of_work
from riftlab.shared.errors import InvalidCursorError, NotFoundError

SessionFactory = async_sessionmaker[AsyncSession]

CURSOR_VERSION = 1
MAX_CURSOR_CHARS = 256


@dataclass(frozen=True, slots=True)
class MatchListItem:
    match: Match
    preparation: PreparationInfo
    champions_present: tuple[tuple[int, str | None], ...]


@dataclass(frozen=True, slots=True)
class MatchPage:
    items: list[MatchListItem]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class MatchDetailData:
    match: Match
    participants: list[ParticipantView]
    preparation: PreparationInfo
    champions: dict[int, ChampionAsset]


def encode_match_cursor(start_at: datetime, match_id: str) -> str:
    payload = {"v": CURSOR_VERSION, "t": start_at.isoformat(), "m": match_id}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_match_cursor(raw: str) -> tuple[datetime, str]:
    """Qualquer defeito levanta InvalidCursorError com texto fixo (o conteúdo
    recebido nunca é ecoado)."""
    if not raw or len(raw) > MAX_CURSOR_CHARS or not raw.isascii():
        raise InvalidCursorError("cursor inválido")
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(
            base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        )
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise InvalidCursorError("cursor inválido") from exc
    if not isinstance(payload, dict) or set(payload) != {"v", "t", "m"}:
        raise InvalidCursorError("cursor inválido")
    version, raw_time, match_id = payload["v"], payload["t"], payload["m"]
    if isinstance(version, bool) or version != CURSOR_VERSION:
        raise InvalidCursorError("cursor inválido")
    if not isinstance(match_id, str) or not (
        MATCH_ID_REAL_PATTERN.fullmatch(match_id) or MATCH_ID_DEMO_PATTERN.fullmatch(match_id)
    ):
        raise InvalidCursorError("cursor inválido")
    if not isinstance(raw_time, str):
        raise InvalidCursorError("cursor inválido")
    try:
        start_at = datetime.fromisoformat(raw_time)
    except ValueError as exc:
        raise InvalidCursorError("cursor inválido") from exc
    if start_at.tzinfo is None:  # sempre com fuso: nunca ambíguo
        raise InvalidCursorError("cursor inválido")
    return start_at, match_id


async def list_matches(
    session_factory: SessionFactory,
    *,
    limit: int,
    cursor: str | None,
    mapping: CatalogMapping | None = None,
) -> MatchPage:
    after = decode_match_cursor(cursor) if cursor else None
    async with read_only_unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        rows = await repo.list_matches_page(after, limit + 1)
        page = rows[:limit]
        jobs = await repo.list_latest_preparation_jobs([m.match_id for m in page])
        champion_ids = await repo.list_champion_ids_for_matches([m.match_id for m in page])
        assets_by_patch: dict[str, dict[int, ChampionAsset]] = {}
        for patch in sorted(
            {m.patch for m in page if not MATCH_ID_DEMO_PATTERN.fullmatch(m.match_id)}
        ):
            patch_matches = [
                m
                for m in page
                if m.patch == patch and not MATCH_ID_DEMO_PATTERN.fullmatch(m.match_id)
            ]
            ids = [item_id for m in patch_matches for item_id in champion_ids[m.match_id]]
            assets_by_patch[patch] = await champion_assets(
                session, mapping, patch_matches[0].match_id, patch, ids
            )

    def present(m: Match) -> tuple[tuple[int, str | None], ...]:
        assets = assets_by_patch.get(m.patch, {})
        return tuple(
            (champion_id, assets[champion_id].name if champion_id in assets else None)
            for champion_id in champion_ids[m.match_id]
        )

    items = [
        MatchListItem(
            m,
            preparation_info(jobs.get(m.match_id)),
            present(m),
        )
        for m in page
    ]
    next_cursor = (
        encode_match_cursor(page[-1].start_at, page[-1].match_id) if len(rows) > limit else None
    )
    return MatchPage(items, next_cursor)


async def get_match_detail(
    session_factory: SessionFactory, match_id: str, mapping: CatalogMapping | None = None
) -> MatchDetailData:
    async with read_only_unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        match = await repo.get_match(match_id)
        if match is None:
            raise NotFoundError(f"match {match_id!r} não encontrado")
        participants = [
            ParticipantView.from_participant(p) for p in await repo.list_participants(match_id)
        ]
        job = await repo.get_latest_preparation_job(match_id)
        # CC-208: só banco (catálogo de campeões já sincronizado); nunca busca o CDN.
        champions = await champion_assets(
            session, mapping, match_id, match.patch, (p.champion_id for p in participants)
        )
    return MatchDetailData(match, participants, preparation_info(job), champions)
