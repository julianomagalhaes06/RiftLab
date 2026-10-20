from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from riftlab.shared.db import Base, CreatedAtMixin


class PlayerProfileModel(CreatedAtMixin, Base):
    """Perfil buscado por Riot ID (CC-1101). Guarda a chave HMAC do Riot ID (`account_key`, a
    mesma da posição ranqueada) e o HMAC do PUUID (`subject_hash`, o mesmo dos participantes
    importados). Nem o Riot ID nem o PUUID são gravados (CC-1103): o Riot ID vem em cada
    pedido."""

    __tablename__ = "player_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    subject_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_icon_id: Mapped[int | None] = mapped_column(Integer)
    summoner_level: Mapped[int | None] = mapped_column(Integer)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
