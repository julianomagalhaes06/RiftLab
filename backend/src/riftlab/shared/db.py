from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import DateTime, MetaData, Uuid, func, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Metadata compartilhada por todos os modelos ORM. Domínio (domain.py de cada
    módulo) nunca importa este arquivo; só repository.py/models.py o fazem."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UuidPkMixin:
    """UUID interno gerado no cliente (uuid4), sem depender de extensao pgcrypto."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


@asynccontextmanager
async def read_only_unit_of_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Transação READ ONLY: o próprio PostgreSQL rejeita qualquer escrita feita
    por este caminho. A instrução precisa ser a primeira da transação."""
    async with unit_of_work(session_factory) as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        yield session


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def unit_of_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Sessao transacional curta: commit ao sair sem erro, rollback em excecao.
    Repositorios nunca fazem commit escondido; quem abre a UoW e o dono da
    transacao. Nenhuma chamada de rede externa deve ocorrer dentro do bloco."""
    async with session_factory() as session:
        async with session.begin():
            yield session
