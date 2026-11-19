"""Linhas de late game a partir do corpus bruto privado (CC-1502). Sem banco; rede nunca.

Mesmo caminho do produto: parser do par bruto, reducer da reconstrução corrente com as regras
empíricas do patch, seleção (`evaluate_decision`) com um recorte permissivo (qualquer campeão e
rota, fila ranqueada, mapa 11), `late_decisions` e o contexto situacional sobre as mesmas
estruturas da tela (`build_timeline`, quadros como `frames_document`).

Também mede a premissa da tolerância: para cada participante, o inventário final reconstruído é
comparado com os itens finais oficiais só nos itens principais, separando participantes sem
incerteza, com incerteza só tolerável e com incerteza que importa.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from riftlab.candidates.domain import ItemRules
from riftlab.catalog.classification import ItemClass, ItemClassificationTable, classify_catalog
from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.catalog.infrastructure.curation import CURATION_RULES_VERSION, curate_data_dragon_items
from riftlab.dataset.late_v2 import decision_row, late_decisions
from riftlab.decisions.domain import MatchContext, ParticipantView
from riftlab.decisions.scope import DecisionScope
from riftlab.decisions.selection import evaluate_decision
from riftlab.decisions.tolerance import TOLERABLE_CLASSES
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.infrastructure.riot_source import parse_raw_pair
from riftlab.matches.timeline import RawEvent, build_timeline, parse_gold_frames
from riftlab.ml.situation import SituationInput, situation_features
from riftlab.preparation.application import frames_document
from riftlab.profiles.domain import Profiles
from riftlab.shared.domain import SourceOrigin
from riftlab.timeline.application import (
    RECONSTRUCTION_ALGORITHM_VERSION,
    _decision_state_from_purchase,
    _load_catalog_items_closure,
    _referenced_item_ids,
    reconstruct_participant,
)
from riftlab.timeline.domain import TimelineEvent
from riftlab.timeline.empirical_rules import (
    RULE_R4S,
    RULE_R4W,
    EmpiricalRules,
    attribute_system_purchases,
)

RANKED_QUEUES = frozenset({420, 440})
SUMMONERS_RIFT = 11


@dataclass(frozen=True, slots=True)
class CatalogBundle:
    catalog: CatalogVersion
    items: tuple[Item, ...]
    by_id: Mapping[int, Item]
    classification: ItemClassificationTable
    excluded_ids: frozenset[int]


def catalog_from_data_dragon(
    item_document: Mapping[str, Any], patch: str, data_dragon_version: str, sha256: str
) -> CatalogBundle:
    """Catálogo curado em memória a partir do item.json do Data Dragon (mesma curadoria do
    produto); o id é derivado do hash, sem banco."""
    curated, excluded = curate_data_dragon_items(item_document["data"])
    catalog_id = uuid.uuid5(uuid.NAMESPACE_URL, f"riftlab-late:{sha256}")
    by_id = {
        i.item_id: Item(catalog_id, i.item_id, i.price, i.recipe, i.attributes, i.flags)
        for i in curated
    }
    excluded_ids = frozenset(i.item_id for i in excluded)
    table = classify_catalog(
        by_id, curation_rules_version=CURATION_RULES_VERSION, excluded_item_ids=excluded_ids
    )
    catalog = CatalogVersion(
        catalog_id, patch, data_dragon_version, sha256, "v1", sha256, CURATION_RULES_VERSION
    )
    return CatalogBundle(catalog, tuple(by_id.values()), by_id, table, excluded_ids)


class _Repo:
    def __init__(self, items: Mapping[int, Item]) -> None:
        self._items = items

    async def get_item(self, catalog_id: uuid.UUID, item_id: int) -> Item | None:
        return self._items.get(item_id)


def _main_counter(items: Iterable[int], table: ItemClassificationTable) -> Counter[int]:
    return Counter(i for i in items if table.classify(i).item_class is ItemClass.MAIN)


def _uncertainty_class(reasons: Iterable[Any], table: ItemClassificationTable) -> str:
    reasons = list(reasons)
    if not reasons:
        return "CLEAN"
    if all(
        r.item_id is not None and table.classify(r.item_id).item_class in TOLERABLE_CLASSES
        for r in reasons
    ):
        return "TOLERABLE"
    return "INTOLERABLE"


def extract_match(
    match_id: str,
    split: str,
    match_body: bytes,
    timeline_body: bytes,
    *,
    bundle: CatalogBundle,
    empirical: EmpiricalRules,
    item_rules: ItemRules,
    profiles: Profiles,
    secret: str,
    runner: asyncio.Runner,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    counts: Counter[str] = Counter()
    source = parse_raw_pair(match_id, match_body, timeline_body, secret)
    if source.map_id != SUMMONERS_RIFT or source.queue not in RANKED_QUEUES:
        counts["MATCH_OUT_OF_SCOPE"] += 1
        return [], counts
    if source.patch != bundle.catalog.game_patch:
        counts["MATCH_OTHER_PATCH"] += 1
        return [], counts
    events = [
        TimelineEvent(match_id, e.source_index, e.timestamp_ms, e.type, e.payload)
        for e in source.events
    ]
    repo = cast(Any, _Repo(bundle.by_id))  # só get_item, como o repositório do banco
    closure = runner.run(
        _load_catalog_items_closure(
            repo, bundle.catalog.id, runner.run(_referenced_item_ids(events))
        )
    )
    enabled = empirical.enabled_for(source.patch, source.queue)
    if RULE_R4W in enabled:
        events = attribute_system_purchases(
            events, [p.participant_id for p in source.participants if p.role == "UTILITY"]
        )
    provenance = empirical.provenance_for(source.patch, source.queue)
    participants = tuple(
        ParticipantView(p.participant_id, p.champion_id, p.team, p.role)
        for p in source.participants
    )
    context = MatchContext(match_id, source.queue, source.map_id, source.patch)
    raw_events = sorted(
        (RawEvent(e.timestamp_ms, e.type, e.payload) for e in events),
        key=lambda e: e.timestamp_ms,
    )
    timelines, kills, objectives = build_timeline(
        raw_events,
        [p.participant_id for p in participants],
        {p.participant_id: p.team for p in participants},
        bundle.classification,
    )
    situation = SituationInput(
        participants,
        timelines,
        kills,
        objectives,
        parse_gold_frames(frames_document(source.frames)),
    )
    counts["MATCHES"] += 1
    rows: list[dict[str, Any]] = []
    for participant in source.participants:
        replay = reconstruct_participant(
            participant_id=participant.participant_id,
            events=events,
            origin=source.origin,
            frames=source.frames,
            catalog_id=bundle.catalog.id,
            catalog_items=closure,
            excluded_item_ids=bundle.excluded_ids,
            enabled_rules=enabled,
            system_item_ids=empirical.system_item_ids if RULE_R4S in enabled else frozenset(),
        )
        kind = _uncertainty_class(replay.reasons, bundle.classification)
        counts[f"PARTICIPANTS_{kind}"] += 1
        if participant.final_items is not None:
            predicted = _main_counter(
                (i for i, n in replay.final_inventory.items for _ in range(n)),
                bundle.classification,
            )
            official = _main_counter(participant.final_items.items, bundle.classification)
            counts[f"FINAL_MAIN_CHECKED_{kind}"] += 1
            if predicted != official:
                counts[f"FINAL_MAIN_MISMATCH_{kind}"] += 1
        scope = DecisionScope(
            "late_any_champion_v1",
            SourceOrigin.COMPLETED_MATCH,
            RANKED_QUEUES,
            SUMMONERS_RIFT,
            frozenset({source.patch}),
            participant.role or "NONE",
            frozenset({participant.champion_id}),
        )
        states = [
            _decision_state_from_purchase(
                match_id, bundle.catalog.id, source.origin, purchase, provenance
            )
            for purchase in replay.purchases
        ]
        points = [
            evaluate_decision(
                state,
                participants=participants,
                match=context,
                catalog=bundle.catalog,
                classification=bundle.classification,
                scope=scope,
            )
            for state in states
        ]
        decisions, diag = late_decisions(
            states,
            points,
            champion_id=participant.champion_id,
            patch=source.patch,
            map_id=source.map_id,
            catalog=bundle.catalog,
            items=bundle.items,
            classification=bundle.classification,
            rules=item_rules,
        )
        for key, value in diag.items():
            counts[f"{participant.role or 'NONE'}:{key}"] += value
            counts[key] += value
        for decision in decisions:
            features = situation_features(
                situation,
                participant.participant_id,
                decision.anchor.view.event_timestamp_ms,
                items=profiles.items,
                champions=profiles.champions,
            )
            rows.append(
                decision_row(
                    decision,
                    match_id=match_id,
                    split=split,
                    participant_id=participant.participant_id,
                    champion_id=participant.champion_id,
                    role=participant.role,
                    context=features,
                    win=participant.score.win,
                )
            )
    return rows, counts


class RawIndex:
    """Localiza o par bruto de cada partida nas lojas privadas (primeira loja que tiver)."""

    def __init__(self, roots: Iterable[Path]) -> None:
        self._where: dict[str, Path] = {}
        for root in roots:
            for ref in sorted(root.rglob("*.match.ref")):
                self._where.setdefault(ref.name.removesuffix(".match.ref"), ref.parent)
        self._stores: dict[Path, DurableRawMatchStore] = {}

    def __contains__(self, match_id: str) -> bool:
        return match_id in self._where

    def read(self, match_id: str) -> tuple[bytes, bytes] | None:
        directory = self._where.get(match_id)
        if directory is None:
            return None
        store = self._stores.get(directory)
        if store is None:
            store = self._stores[directory] = DurableRawMatchStore(directory, create=False)
        match_body = store.read_if_valid_read_only(match_id, "match")
        timeline_body = store.read_if_valid_read_only(match_id, "timeline")
        if not match_body or not timeline_body:
            return None
        return match_body, timeline_body


def ephemeral_secret() -> str:
    """Segredo só para o parser calcular hashes que nunca saem do processo."""
    return os.urandom(32).hex()


__all__ = [
    "RECONSTRUCTION_ALGORITHM_VERSION",
    "CatalogBundle",
    "RawIndex",
    "catalog_from_data_dragon",
    "ephemeral_secret",
    "extract_match",
]
