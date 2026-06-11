"""
Lightweight async repository for tracking extraction jobs.

A real deployment would point `DATABASE_URL` at PostgreSQL; for the local demo
we use SQLite via `aiosqlite`. The repository pattern keeps all persistence
concerns in one place so the service/route layers stay storage-agnostic.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.config import get_settings

settings = get_settings()


class Base(DeclarativeBase):
    """Declarative base for ORM models."""


class JobModel(Base):
    """Persistent record of an asynchronous extraction job."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    correlation_id: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    callback_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# Single engine / sessionmaker for the process.
engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Create tables on startup (idempotent)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobRepository:
    """CRUD operations for `JobModel`, scoped to a single AsyncSession."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        job_id: str,
        correlation_id: str,
        source_type: Optional[str],
        filename: Optional[str],
        callback_url: Optional[str],
    ) -> JobModel:
        now = _now()
        job = JobModel(
            id=job_id,
            status="PENDING",
            correlation_id=correlation_id,
            source_type=source_type,
            filename=filename,
            callback_url=callback_url,
            created_at=now,
            updated_at=now,
        )
        self.session.add(job)
        await self.session.commit()
        return job

    async def get(self, job_id: str) -> Optional[JobModel]:
        result = await self.session.execute(select(JobModel).where(JobModel.id == job_id))
        return result.scalar_one_or_none()

    async def update_status(self, job_id: str, status: str) -> None:
        job = await self.get(job_id)
        if job is None:
            return
        job.status = status
        job.updated_at = _now()
        await self.session.commit()

    async def set_result(self, job_id: str, result: dict[str, Any]) -> None:
        job = await self.get(job_id)
        if job is None:
            return
        job.status = "COMPLETED"
        job.result_json = json.dumps(result, default=str)
        job.error = None
        job.updated_at = _now()
        await self.session.commit()

    async def set_error(self, job_id: str, error: str) -> None:
        job = await self.get(job_id)
        if job is None:
            return
        job.status = "FAILED"
        job.error = error
        job.updated_at = _now()
        await self.session.commit()
