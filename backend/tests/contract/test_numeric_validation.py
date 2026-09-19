"""is_ascii_digits (shared/numeric.py): único verificador de string numérica
externa; e a garantia de que nenhum `isdigit()` solto resta em src/."""

from __future__ import annotations

from pathlib import Path

import pytest

from riftlab.shared.numeric import is_ascii_digits


@pytest.mark.parametrize("value", ["0", "7", "3006", "0007"])
def test_accepts_only_ascii_digit_strings(value: str) -> None:
    assert is_ascii_digits(value) is True


@pytest.mark.parametrize(
    "value", ["", "²", "٣", "３", "①", "12a", "-1", "1.5", " 1", "1 ", None, 3, b"3", True]
)
def test_rejects_everything_else(value: object) -> None:
    assert is_ascii_digits(value) is False


def test_no_bare_isdigit_left_in_src() -> None:
    src = Path(__file__).resolve().parents[2] / "src"
    assert src.is_dir()  # container: /app/src (parents[2]); host: backend/src
    offenders = [
        f"{path.relative_to(src)}:{number}"
        for path in src.rglob("*.py")
        if path.name != "numeric.py"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if ".isdigit()" in line
    ]
    assert offenders == []
