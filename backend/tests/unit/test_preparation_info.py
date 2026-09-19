"""Estado público da preparação (CC-205): estados internos do job -> PENDING /
RUNNING / READY / FAILED, com código de erro só onde é informação útil."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from riftlab.ingestion.domain import ImportJob, ImportJobState, ImportMode, JobKind
from riftlab.preparation.application import preparation_info
from riftlab.shared.api.enums import PreparationStatus

_RETRY_AT = datetime(2026, 9, 19, 12, 0, tzinfo=UTC) + timedelta(minutes=5)


def _job(state: ImportJobState, **fields: object) -> ImportJob:
    return ImportJob(
        id=uuid.uuid4(),
        requested_match_id="DEMO-0002",
        mode=ImportMode.DEMO,
        state=state,
        kind=JobKind.PREPARE,
        **fields,  # type: ignore[arg-type]
    )


def test_no_job_is_not_requested() -> None:
    info = preparation_info(None)
    assert (info.status, info.error_code, info.retry_at, info.attempts) == (
        PreparationStatus.NOT_REQUESTED,
        None,
        None,
        0,
    )


@pytest.mark.parametrize(
    ("state", "status"),
    [
        (ImportJobState.QUEUED, PreparationStatus.PENDING),
        (ImportJobState.RETRY_WAIT, PreparationStatus.PENDING),
        (ImportJobState.RUNNING, PreparationStatus.RUNNING),
        (ImportJobState.SUCCEEDED, PreparationStatus.READY),
        (ImportJobState.FAILED, PreparationStatus.FAILED),
    ],
)
def test_every_job_state_has_a_public_status(
    state: ImportJobState, status: PreparationStatus
) -> None:
    assert preparation_info(_job(state)).status is status


def test_retry_wait_is_pending_with_last_error_and_retry_at() -> None:
    info = preparation_info(
        _job(
            ImportJobState.RETRY_WAIT,
            attempts=2,
            error_code="DATA_DRAGON_UNAVAILABLE",
            retry_at=_RETRY_AT,
        )
    )
    assert info.status is PreparationStatus.PENDING
    assert (info.error_code, info.retry_at, info.attempts) == (
        "DATA_DRAGON_UNAVAILABLE",
        _RETRY_AT,
        2,
    )


def test_failed_keeps_error_code_but_never_a_retry_time() -> None:
    info = preparation_info(
        _job(
            ImportJobState.FAILED,
            attempts=1,
            error_code="TIMELINE_EVENT_INVALID",
            retry_at=_RETRY_AT,
        )
    )
    assert info.status is PreparationStatus.FAILED
    assert info.error_code == "TIMELINE_EVENT_INVALID" and info.retry_at is None


@pytest.mark.parametrize("state", [ImportJobState.RUNNING, ImportJobState.SUCCEEDED])
def test_running_and_ready_never_carry_a_stale_error(state: ImportJobState) -> None:
    info = preparation_info(_job(state, error_code="OLD_ERROR", retry_at=_RETRY_AT, attempts=3))
    assert info.error_code is None and info.retry_at is None and info.attempts == 3
