"""Leitura de pontos de decisão (CC-204): SÓ banco, em transação
READ ONLY. Nunca reconstrói, extrai, abre fonte, acessa rede nem grava.

Restrição pedagógica do modo Estudo (antes de revelar, só informação
pré-decisão; sem resultado final, compra histórica, score ou ordem do modelo):
esta camada NUNCA devolve o item comprado, a classe do alvo, o bloco
retrospectivo, o vetor de features, a qualidade cumulativa nem o `subject_hash`.
Os motivos ligados ao ALVO (`TARGET_*`) são reduzidos a um código neutro, sem
sub-motivos, porque revelariam a classe do item ou que a compra foi desfeita."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from riftlab.catalog.repository import SqlAlchemyCatalogRepository
from riftlab.decisions.application import resolve_current_versions
from riftlab.decisions.domain import (
    REASON_TARGET_NOT_CLASSIFIABLE,
    REASON_TARGET_REVERTED,
    REASON_TARGET_STATUS_UNCERTAIN,
    DecisionError,
    StoredDecisionPoint,
)
from riftlab.decisions.repository import SqlAlchemyDecisionRepository
from riftlab.ingestion.repository import SqlAlchemyIngestionRepository
from riftlab.shared.db import read_only_unit_of_work
from riftlab.shared.errors import DecisionsNotPreparedError, InvalidCursorError, NotFoundError

SessionFactory = async_sessionmaker[AsyncSession]

CURSOR_VERSION = 1
MAX_CURSOR_CHARS = 512
_MAX_INT32 = 2**31 - 1

# Códigos que, se ainda não há extração corrente, significam "não preparada"
# (e não uma falha do servidor).
_NOT_PREPARED_CODES = frozenset({"RECONSTRUCTION_REQUIRED", "ORIGIN_UNRESOLVED"})

# Motivo público neutro no lugar de qualquer TARGET_*.
PUBLIC_TARGET_REASON = "TARGET_NOT_ELIGIBLE"
_TARGET_REASONS = frozenset(
    {REASON_TARGET_NOT_CLASSIFIABLE, REASON_TARGET_REVERTED, REASON_TARGET_STATUS_UNCERTAIN}
)


@dataclass(frozen=True, slots=True)
class CursorPosition:
    """Posição de retomada, vinculada à extração, à partida e ao participante."""

    extraction_id: uuid.UUID
    match_id: str
    participant_id: int
    timestamp_ms: int
    source_index: int
    point_id: uuid.UUID


def encode_cursor(position: CursorPosition) -> str:
    """Cursor OPACO: base64url de JSON versionado. Não é assinado: adulterar a
    posição só troca o ponto de retomada dentro do MESMO escopo já autorizado
    (extração + partida + participante), o que é inofensivo; o resto é
    validado estritamente na leitura."""
    payload = {
        "v": CURSOR_VERSION,
        "x": str(position.extraction_id),
        "m": position.match_id,
        "p": position.participant_id,
        "t": position.timestamp_ms,
        "s": position.source_index,
        "i": str(position.point_id),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_uuid(value: Any) -> uuid.UUID:
    if not isinstance(value, str):
        raise InvalidCursorError("cursor inválido")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise InvalidCursorError("cursor inválido") from exc
    if str(parsed) != value:  # só a forma canônica
        raise InvalidCursorError("cursor inválido")
    return parsed


def decode_cursor(raw: str, *, match_id: str, participant_id: int) -> CursorPosition:
    """Qualquer defeito (base64, JSON, chaves, tipos, escopo) levanta
    InvalidCursorError com texto fixo: o conteúdo recebido nunca é ecoado."""
    if not raw or len(raw) > MAX_CURSOR_CHARS or not raw.isascii():
        raise InvalidCursorError("cursor inválido")
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise InvalidCursorError("cursor inválido") from exc
    if not isinstance(payload, dict) or set(payload) != {"v", "x", "m", "p", "t", "s", "i"}:
        raise InvalidCursorError("cursor inválido")
    if payload["v"] != CURSOR_VERSION or not _is_int(payload["v"]):
        raise InvalidCursorError("cursor inválido")
    for key in ("p", "t", "s"):
        if not _is_int(payload[key]) or not 0 <= payload[key] <= _MAX_INT32:
            raise InvalidCursorError("cursor inválido")
    if payload["m"] != match_id or payload["p"] != participant_id:
        raise InvalidCursorError("cursor inválido")  # de outra partida/participante
    return CursorPosition(
        extraction_id=_parse_uuid(payload["x"]),
        match_id=match_id,
        participant_id=participant_id,
        timestamp_ms=payload["t"],
        source_index=payload["s"],
        point_id=_parse_uuid(payload["i"]),
    )


@dataclass(frozen=True, slots=True)
class PublicReason:
    code: str
    sub_reasons: tuple[str, ...]


def public_reasons(reasons: list[dict[str, Any]]) -> list[PublicReason]:
    """Motivos da API: os de CONTEXTO (MISSING_CATALOG, OUT_OF_SCOPE,
    INVENTORY_UNCERTAIN) com seus sub-motivos literais; todo motivo ligado ao
    ALVO vira um único `TARGET_NOT_ELIGIBLE` sem sub-motivos (revelar a classe
    do item ou que a compra foi desfeita quebraria o modo Estudo)."""
    public: list[PublicReason] = []
    target_emitted = False
    for reason in reasons:
        code = reason["code"]
        if code in _TARGET_REASONS:
            if not target_emitted:
                public.append(PublicReason(PUBLIC_TARGET_REASON, ()))
                target_emitted = True
            continue
        public.append(PublicReason(code, tuple(reason.get("subReasons", []))))
    return public


@dataclass(frozen=True, slots=True)
class DecisionPage:
    items: list[StoredDecisionPoint]
    next_cursor: str | None


RulesApplication = Literal["VALIDATED", "INHERITED", "NONE"]


@dataclass(frozen=True, slots=True)
class DecisionDetail:
    point: StoredDecisionPoint
    inventory: dict[str, Any]
    level: dict[str, Any]
    # nome de cada item do inventário, lido do catálogo FIXADO pelas versões do
    # ponto (linha imutável), nunca do canônico atual; None = sem catálogo ou
    # item ausente dele
    item_names: dict[int, str | None]
    data_dragon_version: str | None = None
    # De onde vieram as regras empíricas da reconstrução: VALIDATED (confirmadas no
    # próprio patch), INHERITED (de `rules_patch`) ou NONE.
    rules_application: RulesApplication = "NONE"
    rules_patch: str | None = None


def rules_origin(provenance: list[dict[str, Any]]) -> tuple[RulesApplication, str | None]:
    """Estados v9 não gravavam `application`: regra ligada ali era sempre validada."""
    if not provenance:
        return "NONE", None
    first = provenance[0]
    patch = first.get("appliedFromPatch")
    application: RulesApplication = (
        "INHERITED" if first.get("application") == "INHERITED" else "VALIDATED"
    )
    return application, patch if isinstance(patch, str) else None


_read_only = read_only_unit_of_work  # transação READ ONLY compartilhada (shared/db.py)


async def list_decision_points(
    session_factory: SessionFactory,
    match_id: str,
    participant_id: int,
    *,
    limit: int,
    cursor: str | None,
    scope_path: Path,
) -> DecisionPage:
    position = (
        decode_cursor(cursor, match_id=match_id, participant_id=participant_id) if cursor else None
    )
    async with _read_only(session_factory) as session:
        ingestion = SqlAlchemyIngestionRepository(session)
        if await ingestion.get_match(match_id) is None:
            raise NotFoundError(f"match {match_id!r} não encontrado")
        if await ingestion.get_participant(match_id, participant_id) is None:
            raise NotFoundError("participante fora da partida")
        decisions = SqlAlchemyDecisionRepository(session)

        after: tuple[int, int, uuid.UUID] | None = None
        if position is not None:
            # A página seguinte usa SEMPRE a extração fixada no cursor (linhas
            # imutáveis), mesmo que outra extração tenha surgido depois.
            extraction = await decisions.get_extraction_by_id(position.extraction_id)
            if extraction is None or extraction.match_id != match_id:
                raise InvalidCursorError("cursor inválido")
            after = (position.timestamp_ms, position.source_index, position.point_id)
        else:
            try:
                current = await resolve_current_versions(session, match_id, scope_path)
            except DecisionError as exc:
                if exc.code in _NOT_PREPARED_CODES:
                    raise DecisionsNotPreparedError("decisões não preparadas") from exc
                raise
            extraction = await decisions.get_extraction(match_id, current.versions)
            if extraction is None:
                raise DecisionsNotPreparedError("decisões não preparadas")
        rows = await decisions.list_points_page(extraction.id, participant_id, after, limit + 1)

    items = rows[:limit]
    next_cursor: str | None = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = encode_cursor(
            CursorPosition(
                extraction_id=extraction.id,
                match_id=match_id,
                participant_id=participant_id,
                timestamp_ms=last.event_timestamp_ms,
                source_index=last.event_source_index,
                point_id=last.id,
            )
        )
    return DecisionPage(items, next_cursor)


async def get_decision_detail(
    session_factory: SessionFactory, decision_id: uuid.UUID
) -> DecisionDetail:
    """Qualquer extração, mesmo antiga: as linhas são imutáveis e as versões
    seguem no corpo."""
    async with _read_only(session_factory) as session:
        decisions = SqlAlchemyDecisionRepository(session)
        point = await decisions.get_point(decision_id)
        if point is None:
            raise NotFoundError(f"decisão {decision_id} não encontrada")
        inventory, level = await decisions.get_pre_decision_context(point.decision_state_id)
        application, rules_patch = rules_origin(
            await decisions.get_rules_provenance(point.decision_state_id)
        )
        item_names: dict[int, str | None] = {}
        catalog_id = point.versions.catalog_id
        catalog_repo = SqlAlchemyCatalogRepository(session)
        catalog = await catalog_repo.get_catalog_version(catalog_id) if catalog_id else None
        for raw_item_id in inventory.get("items", {}):
            item_id = int(raw_item_id)
            item = await catalog_repo.get_item(catalog_id, item_id) if catalog_id else None
            name = item.attributes.get("name") if item is not None else None
            item_names[item_id] = name if isinstance(name, str) and name else None
    return DecisionDetail(
        point,
        inventory,
        level,
        item_names,
        catalog.data_dragon_version if catalog else None,
        application,
        rules_patch,
    )
