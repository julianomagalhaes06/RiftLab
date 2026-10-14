"""CC-703: dataset commands read the scale collection only when it exists (synthetic paths)."""

from __future__ import annotations

from pathlib import Path

from riftlab.cli.main import _raw_directories


def test_raw_directories_add_the_cc703_store_only_when_present(tmp_path: Path) -> None:
    legacy = [
        tmp_path / "cc316" / "raw-exploration",
        tmp_path / "cc316" / "raw-confirmation",
        tmp_path / "cc323" / "raw",
    ]
    assert _raw_directories(tmp_path) == legacy

    (tmp_path / "cc703" / "raw").mkdir(parents=True)
    assert _raw_directories(tmp_path) == [*legacy, tmp_path / "cc703" / "raw"]
