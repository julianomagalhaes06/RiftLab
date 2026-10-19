from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from riftlab.analyses import application
from riftlab.catalog.classification import classify_catalog
from riftlab.catalog.domain import CatalogVersion, Item
from riftlab.ranking.engine import DecisionFacts, Popularity
from riftlab.ranking.inheritance import (
    PopularityCatalog,
    filter_inherited_popularity,
    latest_previous_artifact,
)


def _catalog(patch: str, *, digest: str, catalog_id: uuid.UUID | None = None):
    catalog_id = catalog_id or uuid.uuid4()
    catalog = CatalogVersion(
        catalog_id, patch, f"{patch}.1", digest, "mapping-v1", "a" * 64, "curated_demo_v1"
    )
    return catalog


def _items(catalog: CatalogVersion, *, changed: bool = False, price: int = 100):
    recipe = [{"item": 11, "count": 1}]
    rows = {
        10: Item(catalog.id, 10, price, recipe, flags={"completed": True}),
        11: Item(catalog.id, 11, 300, flags={"completed": False}),
        12: Item(
            catalog.id,
            12,
            400,
            [{"item": 11 if not changed else 99, "count": 1}],
            flags={"completed": True},
        ),
    }
    return rows, classify_catalog(rows, curation_rules_version=catalog.curation_rules_version)


def _identity(catalog: CatalogVersion, *, catalog_id: str | None = None) -> dict[str, str]:
    result = {
        "gamePatch": catalog.game_patch,
        "dataDragonVersion": catalog.data_dragon_version,
        "sha256": catalog.sha256,
        "mappingVersion": catalog.mapping_version,
        "rawHash": catalog.raw_hash,
        "curationRulesVersion": catalog.curation_rules_version,
    }
    if catalog_id is not None:
        result["catalogId"] = catalog_id
    return result


def test_inheritance_filters_every_cell_recomputes_support_and_ignores_price() -> None:
    source_catalog = _catalog("16.19", digest="1" * 64)
    destination_catalog = _catalog("16.20", digest="2" * 64)
    source_items, source_classes = _items(source_catalog)
    target_items, target_classes = _items(destination_catalog, price=999)
    del target_items[12]
    # Removed 12, changed recipe for 13, absent-from-source 99; only 10 survives.
    source_items[13] = Item(
        source_catalog.id, 13, 100, [{"item": 11, "count": 1}], flags={"completed": True}
    )
    source_classes = classify_catalog(source_items, curation_rules_version="curated_demo_v1")
    target_items[13] = Item(
        destination_catalog.id, 13, 100, [{"item": 10, "count": 1}], flags={"completed": True}
    )
    target_classes = classify_catalog(target_items, curation_rules_version="curated_demo_v1")
    artifact = Popularity(
        "popularity_16_19_v2",
        {"51|1": {"10": 19, "12": 5, "13": 7, "99": 4}, "51|2": {"12": 3}},
        "c" * 64,
        _identity(source_catalog),
        "16.19",
    )
    result = filter_inherited_popularity(
        artifact,
        PopularityCatalog(source_catalog, source_items, source_classes),
        PopularityCatalog(destination_catalog, target_items, target_classes),
    )
    assert result.reason_code is None
    assert result.popularity is not None
    assert result.popularity.cells == {"51|1": {"10": 19}}
    assert result.support == 19  # CC-404 policy is >0, not the Part B threshold 20.
    assert result.popularity.score(DecisionFacts(51, 1, None, None), 10) == 1.0


def test_source_identity_is_semantic_hash_bound_and_uuid_is_not_portability_key() -> None:
    source_catalog = _catalog("16.19", digest="1" * 64)
    destination_catalog = _catalog("16.20", digest="2" * 64)
    source_items, source_classes = _items(source_catalog)
    target_items, target_classes = _items(destination_catalog)
    base = PopularityCatalog(source_catalog, source_items, source_classes)
    destination = PopularityCatalog(destination_catalog, target_items, target_classes)

    artifact = Popularity(
        "legacy_v1", {"51|1": {"10": 1}}, "c" * 64, _identity(source_catalog), "16.19"
    )
    resolved = filter_inherited_popularity(artifact, base, destination)
    assert resolved.popularity is not None
    assert resolved.support == 1

    bad_uuid = Popularity(
        "legacy_v1",
        artifact.cells,
        artifact.dataset_sha256,
        _identity(source_catalog, catalog_id=str(uuid.uuid4())),
        "16.19",
    )
    assert filter_inherited_popularity(bad_uuid, base, destination).popularity is not None
    bad_hash = _identity(source_catalog)
    bad_hash["sha256"] = "d" * 64
    unbound = Popularity("legacy_v1", artifact.cells, artifact.dataset_sha256, bad_hash, "16.19")
    assert filter_inherited_popularity(unbound, base, destination).reason_code == (
        "POPULARITY_SOURCE_CATALOG_IDENTITY_MISMATCH"
    )


def test_companion_manifest_requires_exact_artifact_and_dataset_hashes(tmp_path: Path) -> None:
    ranking = tmp_path / "ranking"
    ranking.mkdir()
    artifact_path = ranking / "popularity-16.19-v2.json"
    document = {
        "version": "popularity_16_19_v2",
        "gamePatch": "16.19",
        "datasetSha256": "c" * 64,
        "cells": {"51|1": {"10": 7}},
    }
    artifact_bytes = json.dumps(document, sort_keys=True).encode()
    artifact_path.write_bytes(artifact_bytes)
    manifest_path = ranking / "popularity-16.19-v2.provenance.json"
    manifest = {
        "schemaVersion": 1,
        "artifactSha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "datasetSha256": document["datasetSha256"],
        "sourceCatalogIdentity": _identity(_catalog("16.19", digest="1" * 64)),
    }
    manifest_path.write_text(json.dumps(manifest))
    loaded = latest_previous_artifact(tmp_path, "16.20")
    assert loaded is not None
    assert loaded.source_catalog_identity == manifest["sourceCatalogIdentity"]

    manifest["artifactSha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest))
    loaded = latest_previous_artifact(tmp_path, "16.20")
    assert loaded is not None and loaded.source_catalog_identity is None

    manifest["artifactSha256"] = hashlib.sha256(artifact_bytes).hexdigest()
    manifest["datasetSha256"] = "e" * 64
    manifest_path.write_text(json.dumps(manifest))
    loaded = latest_previous_artifact(tmp_path, "16.20")
    assert loaded is not None and loaded.source_catalog_identity is None


def test_latest_numeric_previous_wins_without_searching_older_after_unbound(
    tmp_path: Path,
) -> None:
    ranking = tmp_path / "ranking"
    ranking.mkdir()
    docs = [
        ("16.9", "popularity_16_9_v4"),
        ("16.10", "popularity_16_10_v1"),
        ("16.20", "future_v9"),
        ("DEMO", "demo_v9"),
    ]
    for patch, version in docs:
        (ranking / f"popularity-{patch}.json").write_text(
            json.dumps({"version": version, "gamePatch": patch, "cells": {}})
        )
    selected = latest_previous_artifact(tmp_path, "16.11")
    assert selected is not None and selected.game_patch == "16.10"
    # Sem vínculo do mais recente: chamadores falham fechados; a função não
    # devolve silenciosamente o artefato 16.9.
    assert selected.source_catalog_identity is None


def test_checked_in_1619_artifact_binds_through_companion_without_changing_bytes(
    tmp_path: Path,
) -> None:
    # Host: <repo>/backend/tests/unit; container de testes: /app/tests/unit com /app/configs.
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "configs").is_dir():
        repo = Path(__file__).resolve().parents[3]
    configs = repo / "configs"
    artifact_path = configs / "ranking/popularity-16.19-v2.json"
    artifact_bytes = artifact_path.read_bytes()
    # Bytes do CC-704; o vínculo vem só do companion.
    assert hashlib.sha256(artifact_bytes).hexdigest() == (
        "af0eb4178c7b3479763777b94fa686f46b908a49df430a2220ae2a66a67a37fa"
    )
    selected = latest_previous_artifact(configs, "16.20")
    assert selected is not None and selected.version == "popularity_16_19_v2"
    identity = selected.source_catalog_identity
    assert identity is not None
    content = {key: identity[key] for key in identity if key not in {"sha256", "catalogId"}}
    assert content == {
        "gamePatch": "16.19",
        "dataDragonVersion": "16.19.1",
        "mappingVersion": "v1",
        "curationRulesVersion": "data_dragon_v5",
        "rawHash": "72c996d05a316ca194c0f5c02b25016a553bd204a974db09b4312c40cee3d477",
    }
    # Mesma identidade de conteúdo que compute_catalog_content_hash grava no catálogo.
    assert (
        identity["sha256"]
        == hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    )

    # Sem o companion, a mesma cópia dos bytes volta a falhar fechada.
    (tmp_path / "ranking").mkdir()
    (tmp_path / "ranking/popularity-16.19-v2.json").write_bytes(artifact_bytes)
    unbound = latest_previous_artifact(tmp_path, "16.20")
    assert unbound is not None and unbound.source_catalog_identity is None
    destination_catalog = _catalog("16.20", digest="2" * 64)
    destination_items, destination_classes = _items(destination_catalog)
    refused = filter_inherited_popularity(
        unbound,
        None,
        PopularityCatalog(destination_catalog, destination_items, destination_classes),
    )
    assert refused.reason_code == "POPULARITY_SOURCE_CATALOG_IDENTITY_UNBOUND"


@pytest.mark.asyncio
async def test_supported_inheritance_keeps_limited_reason_and_source_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ranking = tmp_path / "ranking"
    ranking.mkdir()
    source_catalog = _catalog("16.19", digest="1" * 64)
    destination_catalog = _catalog("16.20", digest="2" * 64)
    source_items, source_classes = _items(source_catalog)
    target_items, target_classes = _items(destination_catalog)
    artifact_document = {
        "version": "popularity_16_19_v2",
        "gamePatch": "16.19",
        "datasetSha256": "c" * 64,
        "cells": {"51|1": {"10": 19}},
    }
    artifact_path = ranking / "popularity-16.19-v2.json"
    artifact_bytes = json.dumps(artifact_document, sort_keys=True).encode()
    artifact_path.write_bytes(artifact_bytes)
    manifest = {
        "schemaVersion": 1,
        "artifactSha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "datasetSha256": artifact_document["datasetSha256"],
        "sourceCatalogIdentity": _identity(source_catalog),
    }
    (ranking / "popularity-16.19-v2.provenance.json").write_text(json.dumps(manifest))

    async def fake_catalog(_session, catalog_id):
        assert catalog_id == source_catalog.id
        return SimpleNamespace(
            catalog=source_catalog,
            items=tuple(source_items.values()),
            classification=source_classes,
        )

    class FakeRepository:
        def __init__(self, _session):
            pass

        async def find_catalog_version_by_content_hash(self, digest):
            assert digest == source_catalog.sha256
            return source_catalog

    monkeypatch.setattr(application, "_catalog", fake_catalog)
    monkeypatch.setattr(application, "SqlAlchemyCatalogRepository", FakeRepository)
    destination = SimpleNamespace(
        catalog=destination_catalog,
        items=tuple(target_items.values()),
        classification=target_classes,
    )
    selected, support, reasons, reference = await application._popularity_for_analysis(
        object(), tmp_path, "16.20", DecisionFacts(51, 1, None, None), destination, None
    )
    assert selected is not None and support == 19
    assert reasons == ["POPULARITY_INHERITED"]
    assert reference is not None and reference["type"] == "INHERITED"
    assert reference["patch"] == "16.19"
    assert reference["rankingVersion"] == "popularity_16_19_v2"
    assert application._support_level(support > 0, reasons) == "LIMITED"


@pytest.mark.asyncio
async def test_supported_own_reference_wins_without_loading_prior_artifact(tmp_path: Path) -> None:
    decision = DecisionFacts(51, 1, None, None)
    own = Popularity("popularity_16_20_v1", {"51|1": {"10": 1}}, game_patch="16.20")
    selected, support, reasons, reference = await application._popularity_for_analysis(
        object(), tmp_path, "16.20", decision, object(), own
    )
    assert selected is own
    assert support == 1 and reasons == []
    assert reference is not None and reference["type"] == "OWN"


def test_inherited_ranking_version_is_marked_and_respects_contract_length() -> None:
    inherited = {"type": "INHERITED"}
    short = Popularity("popularity_16_19_v2", {})
    assert application._ranking_version(short, inherited, "rules_v1") == (
        "popularity_16_19_v2 (herdado)"
    )
    assert application._ranking_version(short, {"type": "OWN"}, "rules_v1") == (
        "popularity_16_19_v2"
    )
    # 40 é o max_length de AnalysisVersions.ranking; o motivo segue marcando a herança.
    long = Popularity("p" * 35, {})
    assert application._ranking_version(long, inherited, "rules_v1") == "p" * 35
