"""Diagnóstico offline do gate REAL a partir de artefatos congelados.

Lê somente arquivos indicados pelo operador. Não abre banco, raw de partidas,
rede, auditor de confirmação ou repositório de decisões. A saída contém apenas
contagens: marginais não são convertidas em interseções pontuais inventadas.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, cast

from riftlab.candidates.rules import load_item_rules
from riftlab.catalog.classification import ItemClass, classify_catalog
from riftlab.catalog.domain import Item
from riftlab.catalog.infrastructure.catalog_mapping import load_catalog_mapping
from riftlab.catalog.infrastructure.curation import CURATION_RULES_VERSION, curate_data_dragon_items

PATCHES = ("16.16", "16.17")
SETS = ("todas-REAL", "exploracao", "confirmacao")


class GateDiagnosticError(ValueError):
    """Artefato ausente, versão trocada ou contagens inconciliáveis."""


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GateDiagnosticError("ARTEFATO_INVALIDO")
    return value


def _intersection_bounds(total: int, left: int, right: int) -> dict[str, int | bool]:
    """Limites exatos de Fréchet quando só há dois marginais publicados."""
    if not 0 <= left <= total or not 0 <= right <= total:
        raise GateDiagnosticError("CONTAGEM_INVALIDA")
    low, high = max(0, left + right - total), min(left, right)
    return {"min": low, "max": high, "exact": low == high}


def _nested_counts(report: dict[str, Any], label: str, path: tuple[str, ...]) -> dict[str, int]:
    value = report["sets"][label]
    for key in path:
        value = value[key]
    return value


def _validate_report(report: dict[str, Any], version: int) -> None:
    if report.get("reconstructionVersion") != version or set(report.get("sets", {})) != set(SETS):
        raise GateDiagnosticError("VERSAO_OU_CONJUNTO_INCOMPATIVEL")
    whole = report["sets"]["todas-REAL"]
    exploration = report["sets"]["exploracao"]
    confirmation = report["sets"]["confirmacao"]
    for key, fields in (
        ("matchesImported", ()),
        ("states", ("total", "preDecisionUncertain")),
        ("points", ("total", "eligible")),
        ("inRecorte", ("points", "eligible")),
    ):
        if not fields:
            if whole[key] != exploration[key] + confirmation[key]:
                raise GateDiagnosticError("PARTICAO_INCONCILIAVEL")
        else:
            for field in fields:
                if whole[key][field] != exploration[key][field] + confirmation[key][field]:
                    raise GateDiagnosticError("PARTICAO_INCONCILIAVEL")
    if set(whole["byPatch"]) != set(PATCHES):
        raise GateDiagnosticError("PATCH_INCOMPATIVEL")
    for field, total in (
        ("matches", whole["matchesImported"]),
        ("points", whole["points"]["total"]),
        ("eligible", whole["points"]["eligible"]),
    ):
        if sum(whole["byPatch"][patch][field] for patch in PATCHES) != total:
            raise GateDiagnosticError("PATCH_INCONCILIAVEL")
    for label in SETS:
        part = report["sets"][label]
        n = part["points"]["total"]
        if any(
            not isinstance(count, int) or count < 0 or count > n
            for count in part["reasons"].values()
        ):
            raise GateDiagnosticError("CONTAGEM_INVALIDA")
        if part["points"]["eligible"] + part["points"]["ineligible"] != n:
            raise GateDiagnosticError("CONTAGEM_INVALIDA")
    for path in (
        ("reasons",),
        ("subReasons",),
        ("inRecorte", "reasons"),
        ("inRecorte", "subReasons"),
    ):
        whole_counts = _nested_counts(report, "todas-REAL", path)
        left_counts = _nested_counts(report, "exploracao", path)
        right_counts = _nested_counts(report, "confirmacao", path)
        for reason in set(whole_counts) | set(left_counts) | set(right_counts):
            if whole_counts.get(reason, 0) != left_counts.get(reason, 0) + right_counts.get(
                reason, 0
            ):
                raise GateDiagnosticError("MOTIVOS_INCONCILIAVEIS")


def _catalog_audit(
    patch: str, catalog_file: Path, rules_file: Path, mapping_file: Path
) -> dict[str, Any]:
    mapping = load_catalog_mapping(mapping_file)
    entry = mapping.resolve(patch)
    if entry is None:
        raise GateDiagnosticError("PATCH_SEM_MAPPING")
    document = _json(catalog_file)
    if document.get("type") != "item" or document.get("version") != entry.data_dragon_version:
        raise GateDiagnosticError("DATA_DRAGON_VERSION_MISMATCH")
    raw = document.get("data")
    if not isinstance(raw, dict):
        raise GateDiagnosticError("CATALOGO_INVALIDO")
    raw = cast(dict[str, Any], raw)
    curated, excluded = curate_data_dragon_items(raw)
    catalog_id = uuid.UUID(int=1)
    items = {
        value.item_id: Item(
            catalog_id, value.item_id, value.price, value.recipe, value.attributes, value.flags
        )
        for value in curated
    }
    classes = classify_catalog(
        items,
        curation_rules_version=CURATION_RULES_VERSION,
        excluded_item_ids=frozenset(value.item_id for value in excluded),
    )
    rules = load_item_rules(rules_file)
    if rules.game_patch != patch or rules.curation_rules_version != CURATION_RULES_VERSION:
        raise GateDiagnosticError("ITEM_RULES_VERSION_MISMATCH")
    class_counts = Counter(value.item_class.value for value in classes.by_item_id.values())
    excluded_counts = Counter(value.reason_code for value in excluded)
    main_ids = [
        item_id
        for item_id, value in classes.by_item_id.items()
        if value.item_class is ItemClass.MAIN
    ]
    high_ids = [item_id for item_id in raw if int(item_id) >= 100_000]
    high_purchasable_map11 = sum(
        raw[item_id].get("gold", {}).get("purchasable") is True
        and raw[item_id].get("maps", {}).get("11") is True
        for item_id in high_ids
    )
    group_definitions = document.get("groups")
    if not isinstance(group_definitions, list):
        raise GateDiagnosticError("CATALOGO_GRUPOS_INVALIDOS")
    return {
        "dataDragonVersion": document["version"],
        "dataDragonSha256": hashlib.sha256(catalog_file.read_bytes()).hexdigest(),
        "rulesVersion": rules.version,
        "rawItems": len(raw),
        "curatedItems": len(curated),
        "curationExcludedByReason": dict(sorted(excluded_counts.items())),
        "classification": dict(sorted(class_counts.items())),
        "highIdRaw": len(high_ids),
        "highIdPurchasableMap11": high_purchasable_map11,
        "rootGroupDefinitions": len(group_definitions),
        "itemStructuredGroupMembershipFields": sum(
            "groups" in value or "group" in value for value in raw.values()
        ),
        "mainClassified": len(main_ids),
        "mainHighIdExcludedByManifest": sum(
            item_id >= 100_000 and rules.high_id_namespace_excluded for item_id in main_ids
        ),
        "mainLowIdBlockedByBothUnknownPolicies": sum(item_id < 100_000 for item_id in main_ids)
        if not rules.unique_item_ids and not rules.exclusive_coverage
        else 0,
        "exclusiveCoverage": rules.exclusive_coverage,
        "uniqueItemIds": rules.unique_item_ids,
        "legalCandidateCountWithCurrentManifest": 0
        if not rules.unique_item_ids or not rules.exclusive_coverage
        else None,
    }


def diagnose_frozen_gate(
    frozen_dir: Path, catalog_dir: Path, rules_dir: Path, mapping_file: Path
) -> dict[str, Any]:
    before = _json(frozen_dir / "51-remeasure-before-v6.json")
    after = _json(frozen_dir / "53-remeasure-after-v7.json")
    confirmation = _json(frozen_dir / "50-confirmation-result.json")
    _validate_report(before, 6)
    _validate_report(after, 7)
    if confirmation.get("partition") != "confirmation":
        raise GateDiagnosticError("PARTICAO_CONFIRMATORIA_INCOMPATIVEL")
    for label in SETS:
        old = dict(before["sets"][label])
        new = dict(after["sets"][label])
        old.pop("reconstructionVersion", None)
        new.pop("reconstructionVersion", None)
        if old != new:
            raise GateDiagnosticError("PARIDADE_V6_V7_INCOMPATIVEL")
    whole = after["sets"]["todas-REAL"]
    n = whole["points"]["total"]
    reasons = whole["reasons"]
    scope = whole["inRecorte"]
    g_final = {}
    for patch in PATCHES:
        row = confirmation["rows"][f"G-FINAL|{patch}"]
        g_final[patch] = {
            key: row[key]
            for key in (
                "k",
                "n",
                "nMin",
                "mMin",
                "matchesContributing",
                "wilsonUpper",
                "epsilon",
                "outcome",
            )
        }
    return {
        "source": "frozen_cc207_artifacts_only",
        "versionsCompared": [6, 7],
        "parity": "same_aggregates",
        "matches": whole["matchesImported"],
        "states": {
            "total": whole["states"]["total"],
            "preDecisionUncertain": whole["states"]["preDecisionUncertain"],
        },
        "points": dict(whole["points"]),
        "byPatch": whole["byPatch"],
        "reasonsMarginal": dict(sorted(reasons.items())),
        "subReasonsMarginal": dict(sorted(whole["subReasons"].items())),
        "inScope": {
            "points": scope["points"],
            "eligible": scope["eligible"],
            "reasonsMarginal": dict(sorted(scope["reasons"].items())),
            "subReasonsMarginal": dict(sorted(scope["subReasons"].items())),
        },
        "intersections": {
            "inventoryAndOutOfScopeAll": _intersection_bounds(
                n, reasons.get("INVENTORY_UNCERTAIN", 0), reasons.get("OUT_OF_SCOPE", 0)
            ),
            "inventoryAndOrdinalInScope": _intersection_bounds(
                scope["points"],
                scope["reasons"].get("INVENTORY_UNCERTAIN", 0),
                scope["subReasons"].get("OUT_OF_SCOPE:MAIN_ITEM_ORDINAL_BEYOND_SECOND", 0),
            ),
            "inventoryAndTargetRevertedInScope": _intersection_bounds(
                scope["points"],
                scope["reasons"].get("INVENTORY_UNCERTAIN", 0),
                scope["reasons"].get("TARGET_REVERTED", 0),
            ),
        },
        "gFinalConfirmationReadOnly": g_final,
        "catalogs": {
            patch: _catalog_audit(
                patch,
                catalog_dir / f"item-{patch}.1.json",
                rules_dir / f"{patch}.json",
                mapping_file,
            )
            for patch in PATCHES
        },
        "notRecounted": [
            "intersecoes pontuais sem marginais determinantes",
            "motivos por patch e por decisao",
            "codigos de orcamento por decisao (nao sao inelegibilidade)",
            "catálogo persistido por ID e decisões do banco dev",
        ],
    }
