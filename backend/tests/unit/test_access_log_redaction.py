"""O retorno do login social traz o código de autorização na URL: ele não vai para o log."""

from __future__ import annotations

import logging

import pytest

from riftlab.observability import configure_logging


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "/api/v1/auth/social/google/callback?state=abc&code=SEGREDO",
            "/api/v1/auth/social/google/callback",
        ),
        (
            "/api/v1/auth/social/discord/callback?code=SEGREDO",
            "/api/v1/auth/social/discord/callback",
        ),
        ("/api/v1/matches?limit=20", "/api/v1/matches?limit=20"),
    ],
)
def test_access_log_drops_only_the_social_callback_query(path: str, expected: str) -> None:
    configure_logging("INFO")
    logger = logging.getLogger("uvicorn.access")
    record = logger.makeRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", path, "1.1", 302),
        None,
    )
    for item in logger.filters:
        item.filter(record)
    assert expected in record.getMessage()
    assert "SEGREDO" not in record.getMessage()
