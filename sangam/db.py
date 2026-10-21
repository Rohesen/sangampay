from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=not _is_sqlite,
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

if _is_sqlite:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_fk(dbapi_conn, _record) -> None:
        # Enforce foreign keys like Postgres does, so ordering bugs show up in development and tests.
        dbapi_conn.execute("PRAGMA foreign_keys=ON")


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (registers tables)

    Base.metadata.create_all(engine)
