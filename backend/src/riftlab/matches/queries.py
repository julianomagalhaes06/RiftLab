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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.infrastructure.catalog_mapping import CatalogMapping
from riftlab.champions.application import ChampionAsset, champion_assets
from riftlab.ingestion.domain import Match, MatchListScope, Participant
from riftlab.ingestion.models import ParticipantFinalItemsModel
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.ingestion.score import ParticipantScore
from riftlab.preparation.application import PreparationInfo, preparation_info
from riftlab.shared.api.common import MATCH_ID_DEMO_PATTERN, MATCH_ID_REAL_PATTERN
from riftlab.shared.db import read_only_unit_of_work
from riftlab.shared.errors import InvalidCursorError, NotFoundError

SessionFactory = async_sessionmaker[AsyncSession]
LocalSubject = Callable[[AsyncSession], Awaitable[str | None]]

CURSOR_VERSION = 1
MAX_CURSOR_CHARS = 256


@dataclass(frozen=True, slots=True)
class MatchListItem:
    match: Match
    preparation: PreparationInfo
    champions_present: tuple[tuple[int, str | None], ...]
    participants: tuple[MatchParticipantFactsView, ...] = ()
    champions: Mapping[int, ChampionAsset] = field(default_factory=dict)
    local_participant_id: int | None = None
    # F1 (CC-1005): (compras prontas para a revisão, compras do 1º ao 5º item) da conta local.
    review_purchases: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class MatchPage:
    items: list[MatchListItem]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class MatchParticipantFactsView:
    """Fatos pós-partida sem campo para subject_hash ou conta própria."""

    participant_id: int
    champion_id: int
    team: int
    role: str | None
    score: ParticipantScore
    final_items: list[int] | None = None
    final_trinket_item_id: int | None = None

    @classmethod
    def from_participant(cls, participant: Participant) -> MatchParticipantFactsView:
        return cls(
            participant.participant_id,
            participant.champion_id,
            participant.team,
            participant.role,
            participant.score,
        )


@dataclass(frozen=True, slots=True)
class MatchDetailData:
    match: Match
    participants: list[MatchParticipantFactsView]
    preparation: PreparationInfo
    champions: dict[int, ChampionAsset]
    local_participant_id: int | None = None


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
    local_subject: LocalSubject | None = None,
    scope: MatchListScope = "all",
) -> MatchPage:
    """`local_subject` resolve, na mesma transação, o hash da conta local; ele só é
    comparado no banco e vira `local_participant_id` (DEMO: participante 1)."""
    after = decode_match_cursor(cursor) if cursor else None
    async with read_only_unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        subject = await local_subject(session) if local_subject is not None else None
        rows = await repo.list_matches_page(after, limit + 1, scope=scope, local_subject=subject)
        page = rows[:limit]
        jobs = await repo.list_latest_preparation_jobs([m.match_id for m in page])
        finals = {
            (f.match_id, f.participant_id): f
            for f in (
                await session.scalars(
                    select(ParticipantFinalItemsModel).where(
                        ParticipantFinalItemsModel.match_id.in_([m.match_id for m in page])
                    )
                )
            ).all()
        }
        facts = await repo.list_participant_facts_for_matches([m.match_id for m in page], subject)
        champion_ids = {
            match_id: [p.champion_id for p, _ in match_facts]
            for match_id, match_facts in facts.items()
        }
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

    def local_id(m: Match) -> int | None:
        if MATCH_ID_DEMO_PATTERN.fullmatch(m.match_id):
            ids = [p.participant_id for p, _ in facts[m.match_id] if p.participant_id == 1]
        else:
            ids = [p.participant_id for p, local in facts[m.match_id] if local]
        return ids[0] if len(ids) == 1 else None  # ambíguo ou ausente: nenhum

    locals_by_match = {m.match_id: lid for m in page if (lid := local_id(m)) is not None}
    async with read_only_unit_of_work(session_factory) as session:
        from riftlab.match_review.application import review_purchase_counts

        review = await review_purchase_counts(session, locals_by_match)

    items = [
        MatchListItem(
            m,
            preparation_info(jobs.get(m.match_id)),
            present(m),
            tuple(
                _with_final(
                    MatchParticipantFactsView.from_participant(p),
                    finals.get((m.match_id, p.participant_id)),
                )
                for p, _ in facts[m.match_id]
            ),
            assets_by_patch.get(m.patch, {}),
            local_id(m),
            review.get(m.match_id),
        )
        for m in page
    ]
    next_cursor = (
        encode_match_cursor(page[-1].start_at, page[-1].match_id) if len(rows) > limit else None
    )
    return MatchPage(items, next_cursor)


async def get_match_detail(
    session_factory: SessionFactory,
    match_id: str,
    mapping: CatalogMapping | None = None,
    local_subject: LocalSubject | None = None,
) -> MatchDetailData:
    async with read_only_unit_of_work(session_factory) as session:
        repo = SqlAlchemyIngestionRepository(session)
        match = await repo.get_match(match_id)
        if match is None:
            raise NotFoundError(f"match {match_id!r} não encontrado")
        rows = await repo.list_participants(match_id)
        finals = {
            f.participant_id: f
            for f in (
                await session.scalars(
                    select(ParticipantFinalItemsModel).where(
                        ParticipantFinalItemsModel.match_id == match_id
                    )
                )
            ).all()
        }
        participants = [
            _with_final(MatchParticipantFactsView.from_participant(p), finals.get(p.participant_id))
            for p in rows
        ]
        subject = await local_subject(session) if local_subject is not None else None
        if MATCH_ID_DEMO_PATTERN.fullmatch(match_id):
            local_ids = [p.participant_id for p in rows if p.participant_id == 1]
        else:
            local_ids = [p.participant_id for p in rows if subject and p.subject_hash == subject]
        job = await repo.get_latest_preparation_job(match_id)
        # CC-208: só banco (catálogo de campeões já sincronizado); nunca busca o CDN.
        champions = await champion_assets(
            session, mapping, match_id, match.patch, (p.champion_id for p in participants)
        )
    return MatchDetailData(
        match,
        participants,
        preparation_info(job),
        champions,
        local_ids[0] if len(local_ids) == 1 else None,
    )


def _with_final(
    view: MatchParticipantFactsView, final: ParticipantFinalItemsModel | None
) -> MatchParticipantFactsView:
    return (
        replace(view, final_items=list(final.items), final_trinket_item_id=final.trinket_item_id)
        if final
        else view
    )
