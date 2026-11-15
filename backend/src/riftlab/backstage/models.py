"""Registro das chamadas à API da Riot para os Bastidores: só o tipo de consulta e o resultado."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.shared.db import Base


class RiotApiCallModel(Base):
    """Uma tentativa HTTP à Riot. `kind` é o rótulo fixo do transporte (account, match-list,
    match, timeline, league, summoner, spectator...); `region` é o roteamento (AMERICAS ou BR1).
    Sem URL, Riot ID, PUUID nem ID de partida."""

    __tablename__ = "riot_api_calls"
    __table_args__ = (
        CheckConstraint("process IN ('api','worker','cli')", name="process_valid"),
        CheckConstraint("duration_ms >= 0 AND attempt >= 1", name="numbers_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    process: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    region: Mapped[str] = mapped_column(String(10), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
