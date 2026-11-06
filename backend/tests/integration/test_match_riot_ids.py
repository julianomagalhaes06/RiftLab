"""CC-1210: Riot ID dos participantes lido do bruto guardado, sem gravar nada."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete

from riftlab.config import Settings
from riftlab.ingestion.infrastructure.raw_store import DurableRawMatchStore
from riftlab.ingestion.models import MatchModel, ParticipantModel
from riftlab.matches.riot_ids import participant_puuid, participant_riot_ids
from riftlab.shared.db import unit_of_work

MATCH = "BR1_9212100001"


def _raw(participants: list[dict]) -> bytes:
    return json.dumps(
        {
            "metadata": {"matchId": MATCH},
            "info": {"gameMode": "CLASSIC", "participants": participants},
        }
    ).encode()


def _store(directory: Path, body: bytes, match_id: str = MATCH) -> None:
    DurableRawMatchStore(directory).store(match_id, "match", body, hashlib.sha256(body).hexdigest())


def test_reads_riot_ids_and_skips_invalid(tmp_path: Path) -> None:
    _store(
        tmp_path,
        _raw(
            [
                {
                    "participantId": 1,
                    "puuid": "synthetic-p1",
                    "riotIdGameName": " Ana ",
                    "riotIdTagline": "BR1",
                },
                {
                    "participantId": 2,
                    "puuid": "synthetic-p2",
                    "riotIdGameName": "Sem#Tag",
                    "riotIdTagline": "BR1",
                },
                {"participantId": 3, "puuid": "synthetic-p3", "summonerName": "Antigo"},
                {"participantId": 4, "riotIdGameName": "Bia", "riotIdTagline": "x\u0000"},
                {"participantId": True, "riotIdGameName": "Bool", "riotIdTagline": "BR1"},
            ]
        ),
    )
    assert participant_riot_ids(str(tmp_path), MATCH) == {1: ("Ana", "BR1")}
    assert participant_riot_ids(str(tmp_path), "DEMO-0008") == {}
    assert participant_riot_ids(str(tmp_path), "BR1_9212100002") == {}  # sem bruto
    # O PUUID identifica a conta mesmo depois de o jogador trocar de Riot ID.
    assert participant_puuid(str(tmp_path), MATCH, 1) == "synthetic-p1"
    assert participant_puuid(str(tmp_path), MATCH, 4) is None  # sem puuid no bruto
    assert participant_puuid(str(tmp_path), MATCH, 9) is None  # participante inexistente
    assert participant_puuid(str(tmp_path), "DEMO-0008", 1) is None
    assert participant_puuid(str(tmp_path), "BR1_9212100002", 1) is None  # sem bruto
    assert participant_riot_ids(str(tmp_path / "nao-existe"), MATCH) == {}


def test_corrupted_raw_returns_nothing(tmp_path: Path) -> None:
    _store(tmp_path, _raw([{"participantId": 1, "riotIdGameName": "Ana", "riotIdTagline": "BR1"}]))
    for blob in (tmp_path / "blobs").iterdir():
        blob.write_bytes(b"{}")  # conteúdo diverge do hash do nome
    assert participant_riot_ids(str(tmp_path), MATCH) == {}


@pytest.mark.asyncio
async def test_match_detail_carries_riot_id_without_puuid(app_client, session_factory, tmp_path):
    _store(
        tmp_path,
        _raw(
            [
                {
                    "participantId": 1,
                    "puuid": "synthetic-p1",
                    "riotIdGameName": "Ana",
                    "riotIdTagline": "BR1",
                },
                {"participantId": 2, "puuid": "synthetic-p2"},
            ]
        ),
    )
    app = app_client._transport.app  # noqa: SLF001 — o teste troca a configuração da app
    app.state.settings = Settings(raw_storage_dir=str(tmp_path))
    try:
        async with unit_of_work(session_factory) as session:
            session.add(
                MatchModel(
                    match_id=MATCH,
                    queue=420,
                    map_id=11,
                    patch="16.20",
                    start_at=datetime(2026, 10, 22, tzinfo=UTC),
                    duration_seconds=1800,
                    raw_hash="synthetic",
                )
            )
            await session.flush()
            for pid in (1, 2):
                session.add(
                    ParticipantModel(
                        match_id=MATCH,
                        participant_id=pid,
                        champion_id=50 + pid,
                        team=100,
                        subject_hash=f"{pid}" * 64,
                    )
                )
        response = await app_client.get(f"/api/v1/matches/{MATCH}")
        assert response.status_code == 200
        participants = {p["participantId"]: p for p in response.json()["participants"]}
        assert (participants[1]["gameName"], participants[1]["tagLine"]) == ("Ana", "BR1")
        assert participants[2]["gameName"] is None and participants[2]["tagLine"] is None
        assert "synthetic-p" not in response.text and "puuid" not in response.text.lower()
        # A lista do Histórico continua sem Riot ID.
        listing = await app_client.get("/api/v1/matches", params={"limit": 100})
        assert "Ana" not in listing.text
    finally:
        async with unit_of_work(session_factory) as session:
            await session.execute(
                delete(ParticipantModel).where(ParticipantModel.match_id == MATCH)
            )
            await session.execute(delete(MatchModel).where(MatchModel.match_id == MATCH))
