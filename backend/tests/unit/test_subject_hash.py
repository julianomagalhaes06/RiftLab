"""subject_hash real (CC-201): HMAC-SHA256 com segredo local, nunca hash
simples nem chave fixa — configuração ausente falha de forma segura."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from riftlab.ingestion.infrastructure.subject_hash import (
    SubjectHashSecretMissingError,
    compute_real_subject_hash,
)

_PUUID = "puuid-sintetico-nao-real-para-teste"  # noqa: S105


def test_compute_real_subject_hash_matches_hmac_sha256_reference() -> None:
    secret = "segredo-local-de-teste-nao-real"  # noqa: S105
    expected = hmac.new(secret.encode(), _PUUID.encode(), hashlib.sha256).hexdigest()
    assert compute_real_subject_hash(_PUUID, secret) == expected


def test_compute_real_subject_hash_is_deterministic() -> None:
    secret = "segredo-local-de-teste-nao-real"  # noqa: S105
    assert compute_real_subject_hash(_PUUID, secret) == compute_real_subject_hash(_PUUID, secret)


def test_compute_real_subject_hash_differs_by_secret() -> None:
    a = compute_real_subject_hash(_PUUID, "segredo-a")
    b = compute_real_subject_hash(_PUUID, "segredo-b")
    assert a != b


def test_compute_real_subject_hash_differs_from_plain_sha256() -> None:
    """Nunca deve degradar para hash simples do puuid — nem por coincidência."""
    secret = "segredo-local-de-teste-nao-real"  # noqa: S105
    plain = hashlib.sha256(_PUUID.encode()).hexdigest()
    assert compute_real_subject_hash(_PUUID, secret) != plain


@pytest.mark.parametrize("missing_secret", [None, ""])
def test_compute_real_subject_hash_requires_secret(missing_secret: str | None) -> None:
    with pytest.raises(SubjectHashSecretMissingError):
        compute_real_subject_hash(_PUUID, missing_secret)


def test_subject_hash_secret_missing_error_never_echoes_puuid() -> None:
    try:
        compute_real_subject_hash(_PUUID, None)
    except SubjectHashSecretMissingError as exc:
        assert _PUUID not in str(exc)
    else:
        pytest.fail("esperava SubjectHashSecretMissingError")
