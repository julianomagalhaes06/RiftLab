import json
import uuid

import pytest
from pipeline_helpers import import_real_match, reset_match, write_prepare_configs

from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.reports.patch_readiness import patch_readiness


@pytest.mark.asyncio
async def test_readiness_is_aggregate_and_never_claims_confirmation(
    session_factory, tmp_path
) -> None:
    match_id = f"BR1_{uuid.uuid4().int % 10**10}"
    patch = "77.77"
    write_prepare_configs(tmp_path, scope_patch=patch, mapped_patches=(patch,))
    try:
        await import_real_match(
            session_factory,
            DurableRawMatchStore(tmp_path / "raw"),
            match_id,
            patch,
            ((120_000, 3006),),
        )
        result = await patch_readiness(session_factory, tmp_path / "scope.yaml")
        row = result["patches"][patch]
        assert row["matches"] == 1
        assert row["minimums"]["G-FINAL"]["status"] == "INSUFFICIENT"
        assert result["confirmationRun"] is False
        assert match_id not in json.dumps(result)
        assert "subject" not in json.dumps(result).lower()
    finally:
        await reset_match(session_factory, match_id)
    empty = await patch_readiness(session_factory, tmp_path / "scope.yaml")
    assert empty["patches"][patch]["matches"] == 0
    assert empty["patches"][patch]["minimums"]["G-FINAL"]["status"] == "INSUFFICIENT"
