from collections.abc import AsyncGenerator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_schema_updates)


def _ensure_schema_updates(sync_conn) -> None:
    inspector = inspect(sync_conn)
    employee_columns = {column["name"] for column in inspector.get_columns("employees")}
    if "is_admin" not in employee_columns:
        sync_conn.execute(text("ALTER TABLE employees ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE"))
    if "is_superuser" not in employee_columns:
        sync_conn.execute(text("ALTER TABLE employees ADD COLUMN is_superuser BOOLEAN NOT NULL DEFAULT FALSE"))
