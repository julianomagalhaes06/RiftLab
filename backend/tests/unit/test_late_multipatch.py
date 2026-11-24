"""CC-1503 (16.23): treino conjunto de patches, patch e elo como categóricas, teste por patch."""

from __future__ import annotations

import gzip
import json
import random
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from riftlab.cli.late import late_app
from riftlab.ml.late_eval import Scored, slices
from riftlab.ml.late_model import (
    CATEGORICAL_CONTEXT,
    ROW_CATEGORICAL,
    FeatureSpace,
    LateArtifact,
    LatePrior,
    decision_block,
    elo_group,
)
from riftlab.profiles.domain import ItemProfile

ITEMS = (3031, 3036, 3072, 3094, 3153, 6672)


def _row(rng: random.Random, split: str, n: int, elo: str | None) -> dict:
    target = ITEMS[(n * 7 + rng.randrange(2)) % len(ITEMS)]
    row = {
        "matchId": f"BR1_{n // 4}",
        "participantId": 1 + n % 4,
        "split": split,
        "championId": 222 if n % 2 else 51,
        "role": "BOTTOM",
        "ordinal": 3 + n % 4,
        "targetItemId": target,
        "targetLegal": True,
        "candidates": [
            {"itemId": i, "remainingCost": 2500 + 10 * i % 300, "ownedComponent": i == target}
            for i in ITEMS
        ],
        "context": {
            "minute": 20.0 + n % 15,
            "enemy_healers": float(n % 3),
            "self_champion_id": "222" if n % 2 else "51",
            "self_role": "BOTTOM",
            "self_primary_tag": "Marksman",
            "top_enemy_tag": "Fighter",
        },
        "completion": {"kind": "FORCED"},
    }
    if elo is not None:
        row["elo"] = elo
        row["patch"] = "16.23"
    return row


def _write_rows(path: Path, elo: str | None, seed: int) -> Path:
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as handle:
        n = 0
        for split, count in (("train", 160), ("validation", 48), ("test", 48)):
            for _ in range(count):
                handle.write(json.dumps(_row(rng, split, n, elo)) + "\n")
                n += 1
    return path


def _item_json(path: Path, bump: int) -> Path:
    data = {str(i): {"name": f"Item {i}", "gold": {"total": 3000 + bump + i % 100}} for i in ITEMS}
    path.write_text(json.dumps({"data": data}))
    return path


def test_elo_group_maps_seed_tiers():
    assert elo_group("CHALLENGER_OR_GRANDMASTER") == "MASTER_PLUS"
    assert elo_group("MASTER") == "MASTER_PLUS"
    assert elo_group("DIAMOND_III") == "DIAMOND"
    assert elo_group(None) == "MIXED" and elo_group("EMERALD") == "MIXED"


def test_patch_and_elo_come_from_the_row_and_old_spaces_ignore_them():
    profiles = {3031: ItemProfile(3031, {"ad": 65.0}, frozenset())}
    row = _row(random.Random(0), "train", 1, "DIAMOND")
    prior = LatePrior.fit([row])
    ids, q = [3031, 3036], np.array([0.7, 0.3])
    new = FeatureSpace.build(
        ["minute"], profiles, {3031: 3400}, (*CATEGORICAL_CONTEXT, *ROW_CATEGORICAL)
    )
    _, categorical = decision_block(row, ids, q, new)
    assert list(categorical[0, -3:]) == ["16.23", "DIAMOND", "3031"]
    old = FeatureSpace.build(["minute"], profiles, {3031: 3400})
    assert old.categorical_names == (*CATEGORICAL_CONTEXT, "cand_item_id")
    assert decision_block(row, ids, q, old)[1].shape == (2, len(CATEGORICAL_CONTEXT) + 1)
    assert new.to_document()["categoricalContext"][-2:] == ["patch", "elo"]
    served = LateArtifact(None, prior, new, 30, {"rowDefaults": {"patch": "16.23", "elo": "X"}})
    bare = {k: v for k, v in row.items() if k not in ("patch", "elo")}
    assert served._with_defaults(bare)["elo"] == "X"
    assert served._with_defaults(row)["elo"] == "DIAMOND"  # a linha manda


def test_elo_slices_are_descriptive_extras():
    row = {"targetItemId": 1, "ordinal": 5, "targetLegal": True, "elo": "MASTER_PLUS"}
    scored = Scored(row, [1, 2], np.array([0.6, 0.4]), np.array([0.5, 0.5]))
    names = slices(row, scored)
    assert {"all", "ordinal:5+", "elo:MASTER_PLUS", "elo:MASTER_PLUS:ordinal:5+"} <= set(names)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_joint_training_serves_the_gate_patch_and_opens_each_test_once(tmp_path: Path):
    private = tmp_path / ".private"
    served = _write_rows(private / "late-16.23.jsonl.gz", "MASTER_PLUS", 1)
    extra = _write_rows(private / "late-16.19.jsonl.gz", None, 2)  # linhas v2: sem patch/elo
    items_23 = _item_json(tmp_path / "item-16.23.json", 0)
    items_19 = _item_json(tmp_path / "item-16.19.json", 50)
    artifact = private / "artifact"
    runner = CliRunner()
    extra_args = [
        "--extra-patch",
        "16.19",
        "--extra-rows",
        str(extra),
        "--extra-item-json",
        str(items_19),
    ]
    result = runner.invoke(
        late_app,
        [
            "train",
            "--rows",
            str(served),
            "--item-json",
            str(items_23),
            "--patch",
            "16.23",
            "--output-dir",
            str(artifact),
            "--iterations",
            "5",
            "--threads",
            "1",
            "--top-k",
            "5",
            *extra_args,
        ],
    )
    assert result.exit_code == 0, result.output
    metadata = json.loads((artifact / "metadata.json").read_text())
    assert metadata["patch"] == "16.23" and metadata["rowDefaults"]["elo"] == "MASTER_PLUS"
    assert [s["patch"] for s in metadata["sources"]] == ["16.23", "16.19"]
    assert metadata["space"]["categoricalContext"][-2:] == ["patch", "elo"]
    assert (artifact / "prior-16.19.json").exists()
    validation = json.loads((artifact / "validation-report.json").read_text())
    assert "16.19" in validation["validationByPatch"]
    assert "elo:MASTER_PLUS" in validation["validation"]
    assert not any(k.startswith("elo:") for k in validation["validationByPatch"]["16.19"]) or (
        "elo:MIXED" in validation["validationByPatch"]["16.19"]
    )

    prereg = tmp_path / "04.md"
    prereg.write_text("gate")
    import hashlib

    (tmp_path / "04.md.sha256").write_text(hashlib.sha256(b"gate").hexdigest() + "  04.md\n")
    test_args = [
        "evaluate-test",
        "--rows",
        str(served),
        "--item-json",
        str(items_23),
        "--patch",
        "16.23",
        "--artifact",
        str(artifact),
        "--preregistration",
        str(prereg),
        *extra_args,
    ]
    first = runner.invoke(late_app, test_args)
    assert first.exit_code == 0, first.output
    report = json.loads((artifact / "test-report.json").read_text())
    secondary = json.loads((artifact / "test-report-16.19.json").read_text())
    assert report["all"]["decisions"] == 48 and secondary["all"]["decisions"] == 48
    assert "elo:MASTER_PLUS" in report and "elo:MIXED" in secondary
    again = runner.invoke(late_app, test_args)
    assert again.exit_code == 1  # teste único

    review = runner.invoke(
        late_app,
        [
            "review-sample",
            "--rows",
            str(served),
            "--item-json",
            str(items_23),
            "--patch",
            "16.23",
            "--artifact",
            str(artifact),
            "--output",
            str(private / "revisao"),
            "--size",
            "10",
        ],
    )
    assert review.exit_code == 0, review.output
    assert len(json.loads((private / "revisao" / "folha-revisao.json").read_text())) == 10

    loaded = LateArtifact.load(artifact, {}, {i: 3000 for i in ITEMS})
    bare = _row(random.Random(3), "test", 5, None)
    ids, base, ctx = loaded.score(bare)  # sem patch/elo: os de referência do artefato
    assert len(ids) == 5 and ctx.sum() == pytest.approx(1.0)
